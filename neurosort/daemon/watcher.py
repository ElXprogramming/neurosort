"""Debounced filesystem event watcher and stabilization daemon."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import fnmatch
import logging
from pathlib import Path
import threading
import time
from typing import Callable, Dict, Optional, Set

from watchdog.events import (
    DirCreatedEvent,
    DirModifiedEvent,
    DirMovedEvent,
    FileCreatedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

from neurosort.config import NeuroSortConfig
from neurosort.core.classifier import SemanticClassifier
from neurosort.core.executor import ExecutionResult, SafeExecutor
from neurosort.extractors import extract_file_context

logger = logging.getLogger("neurosort.watcher")


class StabilizationChecker:
    """Verifies that candidate files are completely written, non-zero, and idle."""

    def __init__(self, check_interval: float = 2.0, max_retries: int = 15):
        self.check_interval = check_interval
        self.max_retries = max_retries

    def wait_until_stable(self, file_path: Path) -> bool:
        """Verify that the file size is non-zero and has not changed across two checks spaced 2s apart.

        Args:
            file_path: Path to target file.

        Returns:
            True if file is stable and ready, False if deleted, empty, or unstable.
        """
        retries = 0
        while retries < self.max_retries:
            if not file_path.exists():
                return False

            try:
                size1 = file_path.stat().st_size
            except OSError:
                return False

            if size1 == 0:
                time.sleep(self.check_interval)
                retries += 1
                continue

            # Wait 2 seconds for stabilization check
            time.sleep(self.check_interval)

            if not file_path.exists():
                return False

            try:
                size2 = file_path.stat().st_size
            except OSError:
                return False

            # Verify size is non-zero and identical across both checks
            if size2 > 0 and size2 == size1:
                # Extra check: attempt to open for reading to confirm write locks released
                try:
                    with file_path.open("rb") as f:
                        f.read(1)
                    return True
                except (PermissionError, OSError):
                    pass

            retries += 1

        logger.warning(f"File did not stabilize after {self.max_retries} checks: {file_path}")
        return False


class DebouncedEventHandler(FileSystemEventHandler):
    """Handles filesystem events with debouncing, ignore pattern filtering, and stabilization."""

    def __init__(
        self,
        config: NeuroSortConfig,
        on_file_ready: Callable[[Path], None],
        debounce_seconds: float = 5.0,
        executor_threads: int = 4,
    ):
        super().__init__()
        self.config = config
        self.on_file_ready = on_file_ready
        self.debounce_seconds = debounce_seconds
        self.stabilizer = StabilizationChecker(check_interval=2.0)
        self.executor = ThreadPoolExecutor(max_workers=executor_threads)

        self._timers: Dict[Path, threading.Timer] = {}
        self._lock = threading.Lock()
        self._active_files: Set[Path] = set()

    def _is_ignored(self, file_path: Path) -> bool:
        """Check if file matches ignore patterns or temporary suffixes."""
        name = file_path.name
        # Ignore hidden files, vim/swap files, temp downloads
        for pattern in self.config.ignore_patterns:
            if fnmatch.fnmatch(name, pattern):
                return True
            if fnmatch.fnmatch(name.lower(), pattern.lower()):
                return True

        if name.startswith((".", "~")):
            return True

        return False

    def on_created(self, event: FileSystemEvent) -> None:
        if isinstance(event, (DirCreatedEvent,)):
            return
        self._schedule_file(Path(event.src_path))

    def on_moved(self, event: FileSystemEvent) -> None:
        if isinstance(event, (DirMovedEvent,)):
            return
        # If moved into or within watched directory, process destination
        dest_path = getattr(event, "dest_path", None)
        if dest_path:
            self._schedule_file(Path(dest_path))

    def on_modified(self, event: FileSystemEvent) -> None:
        if isinstance(event, (DirModifiedEvent,)):
            return
        # If file was modified, reset debounce timer
        self._schedule_file(Path(event.src_path))

    def _schedule_file(self, file_path: Path) -> None:
        """Schedule or reset debounced stabilization check for file."""
        resolved = file_path.resolve()

        if resolved.is_dir() or self._is_ignored(resolved):
            return

        with self._lock:
            # If already processing in worker thread, ignore
            if resolved in self._active_files:
                return

            # Cancel existing timer if still counting down
            if resolved in self._timers:
                self._timers[resolved].cancel()

            # Create new debounced timer
            timer = threading.Timer(
                self.debounce_seconds, self._trigger_stabilization, args=(resolved,)
            )
            self._timers[resolved] = timer
            timer.start()

    def _trigger_stabilization(self, file_path: Path) -> None:
        """Trigger threaded stabilization check once debounce timer fires."""
        with self._lock:
            self._timers.pop(file_path, None)
            self._active_files.add(file_path)

        self.executor.submit(self._run_stabilization_and_process, file_path)

    def _run_stabilization_and_process(self, file_path: Path) -> None:
        """Stabilize file and invoke processing callback."""
        try:
            if self.stabilizer.wait_until_stable(file_path):
                self.on_file_ready(file_path)
        except Exception as exc:
            logger.error(f"Unexpected error processing {file_path}: {exc}", exc_info=True)
        finally:
            with self._lock:
                self._active_files.discard(file_path)

    def shutdown(self) -> None:
        """Cancel all pending timers and shutdown worker pool."""
        with self._lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()
        self.executor.shutdown(wait=False)


class DebouncedFileWatcher:
    """Multi-directory background watcher daemon with debouncing and safe ingestion pipeline."""

    def __init__(
        self,
        config: NeuroSortConfig,
        classifier: Optional[SemanticClassifier] = None,
        executor: Optional[SafeExecutor] = None,
    ):
        self.config = config
        self.classifier = classifier or SemanticClassifier(config)
        self.executor = executor or SafeExecutor(config)
        self.observer = Observer()
        self.handlers: list[DebouncedEventHandler] = []
        self._is_running = False

    def process_file(self, file_path: Path) -> Optional[ExecutionResult]:
        """Extract metadata, semantically classify, and safely organize a ready file."""
        logger.info(f"Ingesting file: {file_path}")
        try:
            context = extract_file_context(file_path)
            classification = self.classifier.classify(context)
            result = self.executor.execute_move(context, classification)
            return result
        except Exception as exc:
            logger.error(
                f"Ingestion failed for {file_path}: {exc}. Original file left intact.",
                exc_info=True,
            )
            return None

    def start(self, blocking: bool = False) -> None:
        """Start monitoring all configured watch directories."""
        if self._is_running:
            return

        for watch_cfg in self.config.watch_paths:
            path = watch_cfg.path
            if not path.exists():
                logger.info(f"Creating intake directory: {path}")
                path.mkdir(parents=True, exist_ok=True)

            handler = DebouncedEventHandler(
                config=self.config,
                on_file_ready=self.process_file,
                debounce_seconds=watch_cfg.debounce_seconds,
            )
            self.handlers.append(handler)
            self.observer.schedule(handler, str(path), recursive=False)
            logger.info(
                f"Scheduled watcher for {path} (debounce={watch_cfg.debounce_seconds}s)"
            )

        self.observer.start()
        self._is_running = True
        logger.info("NeuroSort Watcher Daemon successfully started.")

        if blocking:
            try:
                while self._is_running:
                    time.sleep(1.0)
            except KeyboardInterrupt:
                self.stop()

    def stop(self) -> None:
        """Gracefully stop observer and all debouncing handlers."""
        if not self._is_running:
            return

        logger.info("Stopping NeuroSort Watcher Daemon...")
        self.observer.stop()
        self.observer.join(timeout=5.0)

        for handler in self.handlers:
            handler.shutdown()

        self._is_running = False
        logger.info("NeuroSort Watcher Daemon stopped cleanly.")
