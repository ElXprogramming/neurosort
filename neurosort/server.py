"""Local Web GUI server and REST API for NeuroSort."""

from __future__ import annotations

from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import threading
from typing import Any, Dict, List, Optional
import urllib.parse
import webbrowser

from neurosort.config import (
    DEFAULT_CONFIG_FILENAME,
    NeuroSortConfig,
    load_config,
    save_config,
)
from neurosort.core.classifier import ClassificationResult, SemanticClassifier
from neurosort.core.executor import SafeExecutor
from neurosort.core.ledger import TransactionLedger
from neurosort.daemon.watcher import DebouncedFileWatcher
from neurosort.extractors import extract_file_context

logger = logging.getLogger("neurosort.server")


class NeuroSortServerState:
    """Singleton server state maintaining watcher daemon and configuration instances."""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or Path(DEFAULT_CONFIG_FILENAME)
        self.config: NeuroSortConfig = load_config(self.config_path)
        self.ledger = TransactionLedger(self.config.database_path)
        self.classifier = SemanticClassifier(self.config)
        self.executor = SafeExecutor(self.config, ledger=self.ledger)
        self.watcher: Optional[DebouncedFileWatcher] = None
        self.watcher_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def reload_config(self) -> None:
        with self._lock:
            self.config = load_config(self.config_path)
            self.classifier = SemanticClassifier(self.config)
            self.executor = SafeExecutor(self.config, ledger=self.ledger)

    def is_daemon_running(self) -> bool:
        return self.watcher is not None and self.watcher._is_running

    def start_daemon(self) -> bool:
        with self._lock:
            if self.is_daemon_running():
                return True
            self.watcher = DebouncedFileWatcher(
                self.config, classifier=self.classifier, executor=self.executor
            )
            self.watcher.start(blocking=False)
            return True

    def stop_daemon(self) -> bool:
        with self._lock:
            if self.watcher:
                self.watcher.stop()
                self.watcher = None
            return True


# Global server state instance
STATE: Optional[NeuroSortServerState] = None


class NeuroSortAPIHandler(BaseHTTPRequestHandler):
    """Handles REST API requests and serves embedded interactive GUI application."""

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default HTTP request spam in terminal."""
        pass

    def _send_json(self, data: Any, status: int = HTTPStatus.OK) -> None:
        payload = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(payload)

    def _send_html(self, html: str, status: int = HTTPStatus.OK) -> None:
        payload = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            self._serve_gui()
        elif path == "/api/status":
            self._handle_status()
        elif path == "/api/scan":
            scan_path = query.get("path", ["~/Downloads"])[0]
            self._handle_scan(scan_path)
        elif path == "/api/history":
            limit = int(query.get("limit", [50])[0])
            self._handle_history(limit)
        elif path == "/api/config":
            self._handle_get_config()
        elif path == "/api/fs/browse":
            browse_path = query.get("path", ["~"])[0]
            self._handle_browse(browse_path)
        elif path == "/api/fs/preview":
            preview_path = query.get("path", [""])[0]
            self._handle_preview(preview_path)
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Endpoint not found")

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else "{}"
        try:
            data = json.loads(body) if body else {}
        except Exception:
            data = {}

        if path == "/api/daemon/start":
            assert STATE is not None
            STATE.start_daemon()
            self._send_json({"running": STATE.is_daemon_running()})
        elif path == "/api/daemon/stop":
            assert STATE is not None
            STATE.stop_daemon()
            self._send_json({"running": STATE.is_daemon_running()})
        elif path == "/api/organize":
            self._handle_organize(data)
        elif path == "/api/rollback":
            self._handle_rollback(data)
        elif path == "/api/config":
            self._handle_update_config(data)
        elif path == "/api/fs/commit":
            self._handle_commit_single(data)
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Endpoint not found")

    def _handle_status(self) -> None:
        assert STATE is not None
        cfg = STATE.config
        running = STATE.is_daemon_running()

        history = STATE.ledger.get_history(limit=5)
        total_moved = len(STATE.ledger.get_last_active_moves(limit=10000))

        self._send_json({
            "daemon_running": running,
            "destination_root": str(cfg.destination_root),
            "watch_paths": [str(wp.path) for wp in cfg.watch_paths],
            "ai_provider": cfg.ai_provider.model_dump(),
            "renaming_template": cfg.renaming_template,
            "total_active_moves": total_moved,
            "recent_transactions": [t.model_dump() for t in history],
        })

    def _handle_scan(self, scan_path_str: str) -> None:
        assert STATE is not None
        target_dir = Path(scan_path_str).expanduser().resolve()
        if not target_dir.exists() or not target_dir.is_dir():
            self._send_json({"error": f"Directory not found: {target_dir}"}, status=400)
            return

        import fnmatch

        candidate_files = []
        for item in target_dir.iterdir():
            if item.is_file():
                name = item.name
                ignored = False
                for pat in STATE.config.ignore_patterns:
                    if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(name.lower(), pat.lower()):
                        ignored = True
                        break
                if not ignored and not name.startswith((".", "~")):
                    candidate_files.append(item)

        results = []
        for file_p in candidate_files:
            try:
                ctx = extract_file_context(file_p)
                classification = STATE.classifier.classify(ctx)
                proposed_path = STATE.executor.resolve_destination(classification, file_p)

                results.append({
                    "original_path": str(file_p),
                    "original_name": file_p.name,
                    "size_bytes": ctx.file_size,
                    "mime_type": ctx.mime_type,
                    "created_date": ctx.created_date.isoformat(),
                    "snippet": ctx.extracted_text_snippet[:250],
                    "target_folder": classification.target_folder,
                    "standardized_name": classification.standardized_name,
                    "proposed_full_path": str(proposed_path),
                    "confidence": classification.confidence,
                    "reasoning": classification.reasoning,
                    "category": classification.category,
                    "entity": classification.entity,
                    "tags": classification.tags,
                })
            except Exception as exc:
                logger.error(f"Scan error on {file_p}: {exc}")

        self._send_json({"scan_dir": str(target_dir), "count": len(results), "files": results})

    def _handle_organize(self, data: Dict[str, Any]) -> None:
        assert STATE is not None
        files_to_organize = data.get("files", [])
        if not files_to_organize:
            self._send_json({"error": "No files specified to organize."}, status=400)
            return

        results = []
        for item in files_to_organize:
            src_str = item.get("original_path")
            if not src_str:
                continue
            src_path = Path(src_str)
            if not src_path.exists():
                results.append({
                    "original_path": src_str,
                    "success": False,
                    "error": "Source file no longer exists",
                })
                continue

            try:
                ctx = extract_file_context(src_path)
                classification = ClassificationResult(
                    target_folder=item.get("target_folder", "Documents/Work"),
                    standardized_name=item.get("standardized_name", src_path.name),
                    confidence=float(item.get("confidence", 0.9)),
                    reasoning=item.get("reasoning", "Organized via GUI"),
                    category=item.get("category", "Work"),
                    entity=item.get("entity", "general"),
                    tags=item.get("tags", []),
                )
                res = STATE.executor.execute_move(ctx, classification, dry_run=False)
                results.append({
                    "original_path": src_str,
                    "target_path": str(res.target_path),
                    "tx_id": res.tx_id,
                    "success": res.success,
                    "error": res.error,
                })
            except Exception as exc:
                results.append({
                    "original_path": src_str,
                    "success": False,
                    "error": str(exc),
                })

        self._send_json({"executed": len(results), "results": results})

    def _handle_history(self, limit: int) -> None:
        assert STATE is not None
        history = STATE.ledger.get_history(limit=limit)
        self._send_json({"count": len(history), "transactions": [t.model_dump() for t in history]})

    def _handle_rollback(self, data: Dict[str, Any]) -> None:
        assert STATE is not None
        tx_id = data.get("tx_id")
        if not tx_id:
            self._send_json({"error": "Missing tx_id"}, status=400)
            return

        res = STATE.executor.rollback_transaction(int(tx_id))
        self._send_json(res.model_dump())

    def _handle_get_config(self) -> None:
        assert STATE is not None
        self._send_json(STATE.config.model_dump(mode="json"))

    def _handle_update_config(self, data: Dict[str, Any]) -> None:
        assert STATE is not None
        try:
            new_cfg = NeuroSortConfig.model_validate(data)
            save_config(new_cfg, STATE.config_path)
            STATE.reload_config()
            self._send_json({"success": True, "config": new_cfg.model_dump(mode="json")})
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=400)

    def _handle_browse(self, dir_path_str: str) -> None:
        """Browse real filesystem folders and files safely."""
        try:
            target_p = Path(os.path.expanduser(dir_path_str)).resolve()
            if not target_p.exists() or not target_p.is_dir():
                target_p = Path.home()

            parent_p = str(target_p.parent) if target_p.parent != target_p else None

            # Generate breadcrumbs
            home = Path.home()
            breadcrumbs = []
            try:
                rel = target_p.relative_to(home)
                breadcrumbs.append({"name": "~", "path": str(home)})
                curr = home
                for part in rel.parts:
                    curr = curr / part
                    breadcrumbs.append({"name": part, "path": str(curr)})
            except Exception:
                curr = Path(target_p.anchor)
                breadcrumbs.append({"name": str(target_p.anchor), "path": str(target_p.anchor)})
                for part in target_p.parts[1:]:
                    curr = curr / part
                    breadcrumbs.append({"name": part, "path": str(curr)})

            directories = []
            files = []

            for item in sorted(target_p.iterdir(), key=lambda x: x.name.lower()):
                if item.name.startswith("."):
                    continue
                try:
                    stat = item.stat()
                    mtime_str = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
                    if item.is_dir():
                        directories.append({
                            "name": item.name,
                            "path": str(item),
                            "modified": mtime_str,
                        })
                    elif item.is_file():
                        files.append({
                            "name": item.name,
                            "path": str(item),
                            "size_bytes": stat.st_size,
                            "size_formatted": format_file_size(stat.st_size),
                            "extension": item.suffix.lower(),
                            "modified": mtime_str,
                        })
                except (PermissionError, OSError):
                    continue

            self._send_json({
                "current_path": str(target_p),
                "parent_path": parent_p,
                "breadcrumbs": breadcrumbs,
                "directories": directories,
                "files": files,
                "total_items": len(directories) + len(files),
            })
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=500)

    def _handle_preview(self, file_path_str: str) -> None:
        """Inspect a single real file non-destructively and project its Future Sight state."""
        assert STATE is not None
        try:
            file_path = Path(os.path.expanduser(file_path_str)).resolve()
            if not file_path.exists() or not file_path.is_file():
                self._send_json({"error": f"File not found: {file_path_str}"}, status=404)
                return

            ctx = extract_file_context(file_path)
            classification = STATE.classifier.classify(ctx)
            proposed_dest = STATE.executor.resolve_destination(classification, file_path)

            stat = file_path.stat()
            res = {
                "original": {
                    "name": file_path.name,
                    "path": str(file_path),
                    "parent_dir": str(file_path.parent),
                    "size_bytes": stat.st_size,
                    "size_formatted": format_file_size(stat.st_size),
                    "mime_type": ctx.mime_type,
                    "created_date": ctx.created_date.strftime("%Y-%m-%d %H:%M"),
                    "snippet": (
                        ctx.extracted_text_snippet[:800]
                        if ctx.extracted_text_snippet
                        else "(Binary or media content)"
                    ),
                    "raw_metadata": ctx.raw_metadata,
                },
                "future_sight": {
                    "target_folder": classification.target_folder,
                    "standardized_name": classification.standardized_name,
                    "destination_full_path": str(proposed_dest),
                    "confidence": classification.confidence,
                    "category": classification.category,
                    "entity": classification.entity,
                    "tags": classification.tags,
                    "reasoning": classification.reasoning,
                },
            }
            self._send_json(res)
        except Exception as exc:
            logger.error(f"Error projecting Future Sight for {file_path_str}: {exc}")
            self._send_json({"error": str(exc)}, status=500)

    def _handle_commit_single(self, data: Dict[str, Any]) -> None:
        """Explicitly apply a single Future Sight projection move upon user request."""
        assert STATE is not None
        file_path_str = data.get("file_path")
        if not file_path_str:
            self._send_json({"error": "Missing file_path"}, status=400)
            return

        file_p = Path(os.path.expanduser(file_path_str)).resolve()
        if not file_p.exists():
            self._send_json({"error": "File does not exist"}, status=404)
            return

        try:
            ctx = extract_file_context(file_p)
            classification = ClassificationResult(
                target_folder=data.get("target_folder", "Documents/Work"),
                standardized_name=data.get("standardized_name", file_p.name),
                confidence=float(data.get("confidence", 0.9)),
                reasoning=data.get("reasoning", "Committed via Future Sight GUI"),
                category=data.get("category", "Work"),
                entity=data.get("entity", "general"),
                tags=data.get("tags", []),
            )
            result = STATE.executor.execute_move(ctx, classification, dry_run=False)
            self._send_json({
                "success": result.success,
                "source_path": str(result.source_path),
                "target_path": str(result.target_path),
                "tx_id": result.tx_id,
                "error": result.error,
            })
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=500)

    def _serve_gui(self) -> None:
        """Serve the interactive web interface."""
        for candidate in [
            Path(__file__).parent / "ui" / "index.html",
            Path(__file__).parent.parent / "neurosort" / "ui" / "index.html",
            Path(__file__).parent.parent / "index.html",
        ]:
            if candidate.exists():
                self._send_html(candidate.read_text(encoding="utf-8"))
                return
        self._send_html("<h1>NeuroSort Web Interface</h1><p>GUI index.html not found.</p>")


def format_file_size(bytes_num: int) -> str:
    """Format bytes into human-readable string."""
    num = float(bytes_num)
    for unit in ["B", "KB", "MB", "GB"]:
        if num < 1024.0:
            return f"{num:.1f} {unit}" if unit != "B" else f"{int(num)} B"
        num /= 1024.0
    return f"{num:.1f} TB"


def start_server(
    config_path: Optional[Path] = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    """Launch the NeuroSort Local Web GUI server."""
    global STATE
    STATE = NeuroSortServerState(config_path)

    server = ThreadingHTTPServer((host, port), NeuroSortAPIHandler)
    url = f"http://{host}:{port}"

    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if STATE and STATE.is_daemon_running():
            STATE.stop_daemon()
        server.server_close()
