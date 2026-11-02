"""Command-line interface and interactive TUI for NeuroSort using Typer and Rich."""

from __future__ import annotations

import fnmatch
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Optional

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn
from rich.table import Table
import typer

from neurosort import __version__
from neurosort.config import (
    DEFAULT_CONFIG_FILENAME,
    NeuroSortConfig,
    get_default_config,
    load_config,
    save_config,
)
from neurosort.core.classifier import SemanticClassifier
from neurosort.core.executor import SafeExecutor
from neurosort.core.ledger import TransactionLedger
from neurosort.daemon.watcher import DebouncedFileWatcher
from neurosort.extractors import extract_file_context

app = typer.Typer(
    name="neurosort",
    help="🧠 NeuroSort: Modern, local-first, safe, and intelligent automatic file management daemon.",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()


def _print_banner() -> None:
    """Print the stylized NeuroSort banner."""
    banner_text = (
        "[bold cyan]NeuroSort[/bold cyan] [dim]v"
        + __version__
        + "[/dim]\n"
        "[dim]Local-first • Zero-data-loss • Semantic File Management[/dim]"
    )
    console.print(Panel(banner_text, border_style="cyan", expand=False))


@app.command("init")
def cmd_init(
    config_path: Path = typer.Option(
        Path(DEFAULT_CONFIG_FILENAME),
        "--path",
        "-p",
        help="Path where config.yaml should be created.",
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite existing configuration file without prompting."
    ),
) -> None:
    """Initialize NeuroSort configuration and scaffold recommended target directories."""
    _print_banner()

    target = config_path.resolve()
    if target.exists() and not force:
        confirm = typer.confirm(
            f"Configuration file already exists at {target}. Overwrite?", default=False
        )
        if not confirm:
            console.print("[yellow]Initialization cancelled.[/yellow]")
            raise typer.Exit(0)

    cfg = get_default_config()
    save_config(cfg, target)

    # Scaffold target taxonomy folders under destination_root
    dest_root = cfg.destination_root
    dest_root.mkdir(parents=True, exist_ok=True)

    created_folders = []
    for cluster in cfg.rules_precedence.taxonomy_clusters.keys():
        folder = dest_root / cluster
        folder.mkdir(parents=True, exist_ok=True)
        created_folders.append(cluster)

    # Also scaffold watch paths
    for wp in cfg.watch_paths:
        wp.path.mkdir(parents=True, exist_ok=True)

    console.print(
        f"[bold green]✔ Successfully created configuration at:[/bold green] [cyan]{target}[/cyan]"
    )
    console.print(
        f"[bold green]✔ Destination root established at:[/bold green] [cyan]{dest_root}[/cyan]"
    )

    table = Table(title="Scaffolded Taxonomy Directories", box=box.ROUNDED)
    table.add_column("Category Cluster", style="cyan")
    table.add_column("Absolute Path", style="dim")
    for f in created_folders:
        table.add_row(f, str(dest_root / f))
    console.print(table)
    console.print("\n[bold]Next steps:[/bold]")
    console.print("  • Preview organization: [cyan]neurosort scan ~/Downloads --dry-run[/cyan]")
    console.print("  • Start live watcher:   [cyan]neurosort start[/cyan]")


@app.command("start")
def cmd_start(
    config_file: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to custom config.yaml"
    ),
) -> None:
    """Start the background debounced file watcher daemon."""
    _print_banner()

    cfg = load_config(config_file)
    classifier = SemanticClassifier(cfg)
    executor = SafeExecutor(cfg)
    watcher = DebouncedFileWatcher(cfg, classifier=classifier, executor=executor)

    table = Table(title="Daemon Configuration", box=box.ROUNDED)
    table.add_column("Property", style="bold cyan")
    table.add_column("Value", style="green")

    for wp in cfg.watch_paths:
        table.add_row(f"Watch Path ({wp.debounce_seconds}s debounce)", str(wp.path))
    table.add_row("Destination Root", str(cfg.destination_root))
    table.add_row(
        "AI Provider",
        f"{cfg.ai_provider.provider.upper()} ({cfg.ai_provider.model}) @ {cfg.ai_provider.base_url}",
    )
    table.add_row("Transaction Ledger", str(cfg.database_path))
    console.print(table)

    console.print("\n[bold yellow]⚡ Watcher active. Press Ctrl+C to stop.[/bold yellow]\n")

    try:
        watcher.start(blocking=True)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopping watcher daemon...[/yellow]")
        watcher.stop()
        console.print("[green]Watcher daemon stopped cleanly.[/green]")


@app.command("scan")
def cmd_scan(
    path: Path = typer.Argument(..., help="Directory to scan and organize"),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Preview proposed moves without moving any files on disk.",
    ),
    config_file: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to custom config.yaml"
    ),
) -> None:
    """Scan an existing directory and organize files with content extraction & AI classification."""
    _print_banner()

    scan_dir = path.resolve()
    if not scan_dir.exists() or not scan_dir.is_dir():
        console.print(f"[bold red]Error:[/bold red] '{scan_dir}' is not a valid directory.")
        raise typer.Exit(1)

    cfg = load_config(config_file)
    if dry_run:
        cfg.dry_run = True

    classifier = SemanticClassifier(cfg)
    executor = SafeExecutor(cfg)

    # Gather candidate files
    candidate_files = []
    for item in scan_dir.iterdir():
        if item.is_file():
            name = item.name
            ignored = False
            for pat in cfg.ignore_patterns:
                if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(name.lower(), pat.lower()):
                    ignored = True
                    break
            if not ignored and not name.startswith((".", "~")):
                candidate_files.append(item)

    if not candidate_files:
        console.print(f"[yellow]No candidate files found to organize in {scan_dir}.[/yellow]")
        raise typer.Exit(0)

    console.print(
        f"Found [bold cyan]{len(candidate_files)}[/bold cyan] candidate files in [dim]{scan_dir}[/dim]\n"
    )

    results = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Analyzing and classifying...", total=len(candidate_files))

        for file_p in candidate_files:
            progress.update(task, description=f"Inspecting [bold]{file_p.name[:25]}[/bold]...")
            try:
                context = extract_file_context(file_p)
                classification = classifier.classify(context)
                res = executor.execute_move(context, classification, dry_run=dry_run)
                results.append(res)
            except Exception as exc:
                console.print(f"[red]Error processing {file_p.name}: {exc}[/red]")
            progress.advance(task)

    # Present formatted Rich Table
    mode_label = "[yellow](DRY RUN PREVIEW)[/yellow]" if dry_run else "[green](EXECUTED)[/green]"
    table = Table(title=f"NeuroSort Organization Results {mode_label}", box=box.ROUNDED)
    table.add_column("Original File", style="cyan", max_width=25, overflow="ellipsis")
    table.add_column("Target Folder", style="magenta", max_width=22)
    table.add_column("Standardized Name", style="bright_white", max_width=32, overflow="ellipsis")
    table.add_column("Conf.", style="bold", justify="right")
    table.add_column("Status", justify="center")

    success_count = 0
    for r in results:
        conf_style = "green" if r.classification.confidence >= 0.8 else "yellow"
        conf_str = f"[{conf_style}]{r.classification.confidence:.2f}[/{conf_style}]"

        if dry_run:
            status_str = "[yellow]PREVIEW[/yellow]"
        elif r.success:
            status_str = "[bold green]MOVED[/bold green]"
            success_count += 1
        else:
            status_str = f"[bold red]FAILED[/bold red]\n[dim]{r.error}[/dim]"

        table.add_row(
            r.source_path.name,
            r.classification.target_folder,
            r.target_path.name,
            conf_str,
            status_str,
        )

    console.print(table)

    if dry_run:
        console.print(
            "\n[dim]Dry-run completed. No files were modified. To execute, run without --dry-run.[/dim]"
        )
    else:
        console.print(
            f"\n[bold green]✔ Successfully organized {success_count}/{len(results)} files.[/bold green]"
        )
        console.print("[dim]Use 'neurosort undo' to revert changes at any time.[/dim]")


@app.command("undo")
def cmd_undo(
    last_n: int = typer.Option(1, "--last", "-l", help="Number of recent operations to revert."),
    tx_id: Optional[int] = typer.Option(
        None, "--id", "-i", help="Specific transaction ID to rollback."
    ),
    undo_all: bool = typer.Option(
        False, "--all", "-a", help="Reverse ALL previous active file movements in ledger."
    ),
    config_file: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to custom config.yaml"
    ),
) -> None:
    """Reverse recent file movements back to their original source paths using the SQLite ledger."""
    _print_banner()

    cfg = load_config(config_file)
    ledger = TransactionLedger(cfg.database_path)
    executor = SafeExecutor(cfg, ledger=ledger)

    if tx_id is not None:
        targets = [tx_id]
    elif undo_all:
        active_moves = ledger.get_last_active_moves(limit=100000)
        if not active_moves:
            console.print("[yellow]No active file movements found in ledger to undo.[/yellow]")
            raise typer.Exit(0)
        targets = [rec.id for rec in active_moves]
    else:
        active_moves = ledger.get_last_active_moves(limit=last_n)
        if not active_moves:
            console.print("[yellow]No active file movements found in ledger to undo.[/yellow]")
            raise typer.Exit(0)
        targets = [rec.id for rec in active_moves]

    table = Table(title="NeuroSort Rollback Results", box=box.ROUNDED)
    table.add_column("Tx ID", justify="right", style="cyan")
    table.add_column("Current File Path", style="dim", max_width=35, overflow="ellipsis")
    table.add_column("Restored Source Path", style="green", max_width=35, overflow="ellipsis")
    table.add_column("Result", justify="center")

    reverted_count = 0
    for target_id in targets:
        result = executor.rollback_transaction(target_id)
        if result.success:
            reverted_count += 1
            res_str = "[bold green]REVERTED[/bold green]"
        else:
            res_str = f"[bold red]FAILED[/bold red]\n[dim]{result.error}[/dim]"

        table.add_row(
            str(target_id),
            str(result.current_target.name),
            str(result.restored_path.name),
            res_str,
        )

    console.print(table)
    console.print(
        f"\n[bold green]✔ Rollback completed ({reverted_count}/{len(targets)} operations restored).[/bold green]"
    )


@app.command("history")
def cmd_history(
    limit: int = typer.Option(20, "--limit", "-n", help="Number of records to show."),
    config_file: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to custom config.yaml"
    ),
) -> None:
    """Display recent file transactions, classifications, and status from the SQLite ledger."""
    _print_banner()

    cfg = load_config(config_file)
    ledger = TransactionLedger(cfg.database_path)
    records = ledger.get_history(limit=limit)

    if not records:
        console.print(f"[yellow]Transaction ledger is empty ({cfg.database_path}).[/yellow]")
        raise typer.Exit(0)

    table = Table(title=f"Recent Transactions (Last {len(records)})", box=box.ROUNDED)
    table.add_column("ID", justify="right", style="bold cyan")
    table.add_column("Timestamp", style="dim")
    table.add_column("Original Source", style="dim", no_wrap=True)
    table.add_column("Organized Destination", style="bright_white", no_wrap=True)
    table.add_column("SHA-256", style="dim", max_width=10)
    table.add_column("Status", justify="center")

    for rec in records:
        status_style = (
            "[bold green]MOVED[/bold green]"
            if rec.status == "MOVED"
            else ("[bold yellow]REVERTED[/bold yellow]" if rec.status == "REVERTED" else "[red]FAILED[/red]")
        )
        ts_str = rec.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        short_hash = f"{rec.file_sha256[:8]}..." if rec.file_sha256 else "n/a"

        table.add_row(
            str(rec.id),
            ts_str,
            Path(rec.source_path).name,
            Path(rec.target_path).name,
            short_hash,
            status_style,
        )

    console.print(table)


@app.command("ui")
def cmd_ui(
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Bind host address."),
    port: int = typer.Option(8765, "--port", "-p", help="Server port."),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Do not automatically open the default web browser."
    ),
    config_file: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to custom config.yaml"
    ),
) -> None:
    """Launch the NeuroSort Local Interactive Web GUI Dashboard."""
    _print_banner()
    console.print(
        f"[bold green]🚀 Starting NeuroSort Web GUI at:[/bold green] [cyan]http://{host}:{port}[/cyan]"
    )
    console.print("[dim]Press Ctrl+C to stop the GUI server.[/dim]\n")
    from neurosort.server import start_server

    start_server(
        config_path=config_file,
        host=host,
        port=port,
        open_browser=not no_browser,
    )


@app.command("gui", hidden=True)
def cmd_gui(
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Bind host address."),
    port: int = typer.Option(8765, "--port", "-p", help="Server port."),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Do not automatically open the default web browser."
    ),
    config_file: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to custom config.yaml"
    ),
) -> None:
    """Alias for 'neurosort ui'."""
    cmd_ui(host=host, port=port, no_browser=no_browser, config_file=config_file)


# -----------------------------------------------------------------------------
# 🤖 24/7 BACKGROUND SYSTEMD SERVICE AUTOMATION
# -----------------------------------------------------------------------------
service_app = typer.Typer(
    help="⚙️ Manage persistent background systemd service for 24/7 automated file sorting.",
    no_args_is_help=True,
)
app.add_typer(service_app, name="service")


def _get_systemd_user_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def _get_neurosort_bin() -> Path:
    which_bin = shutil.which("neurosort")
    if which_bin:
        return Path(which_bin).resolve()
    venv_bin = Path(sys.executable).parent / "neurosort"
    if venv_bin.exists():
        return venv_bin.resolve()
    return Path(sys.executable)


@service_app.command("install")
def cmd_service_install(
    config_file: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to config.yaml"
    ),
    start_now: bool = typer.Option(
        True, "--start/--no-start", help="Immediately start service after installation."
    ),
) -> None:
    """Install and enable NeuroSort as a persistent systemd user service (starts automatically on login)."""
    _print_banner()
    if not shutil.which("systemctl"):
        console.print("[bold red]Error: systemctl command not found. Systemd is required for user services.[/bold red]")
        raise typer.Exit(1)

    bin_path = _get_neurosort_bin()
    cfg_path = (config_file or Path.cwd() / "config.yaml").resolve()
    if not cfg_path.exists():
        cfg_path = Path.home() / ".neurosort" / "config.yaml"

    systemd_dir = _get_systemd_user_dir()
    systemd_dir.mkdir(parents=True, exist_ok=True)
    service_file = systemd_dir / "neurosort.service"

    exec_cmd = f"{bin_path} start"
    if cfg_path.exists():
        exec_cmd += f" --config {cfg_path}"

    service_content = f"""[Unit]
Description=NeuroSort Automatic File Organization Daemon
Documentation=https://github.com/ElXprogramming/neurosort
After=network.target

[Service]
Type=simple
ExecStart={exec_cmd}
WorkingDirectory={cfg_path.parent if cfg_path.exists() else Path.home()}
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
"""
    service_file.write_text(service_content, encoding="utf-8")
    console.print(f"[green]✓ Wrote systemd service definition to:[/green] [cyan]{service_file}[/cyan]")

    try:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "neurosort.service"], check=True)
        if start_now:
            subprocess.run(["systemctl", "--user", "restart", "neurosort.service"], check=True)
            console.print("[bold green]✓ NeuroSort background service active & watching your folders![/bold green]")
        else:
            console.print("[green]✓ NeuroSort background service enabled.[/green]")
    except subprocess.CalledProcessError as exc:
        console.print(f"[bold red]Failed to configure systemd service:[/bold red] {exc}")
        raise typer.Exit(1)

    panel_text = (
        "[bold cyan]NeuroSort is now automated 24/7 in the background![/bold cyan]\n\n"
        "• [bold green]Continuous Ingestion:[/bold green] Automatically organizes files whenever you download.\n"
        "• [bold green]Login Persistence:[/bold green] Automatically starts on boot/login.\n"
        "• [bold green]Check Status:[/bold green] [bold cyan]neurosort service status[/bold cyan]\n"
        "• [bold green]Stream Live Logs:[/bold green] [bold cyan]neurosort service logs -f[/bold cyan]\n"
        "• [bold green]Stop Anytime:[/bold green] [bold cyan]neurosort service stop[/bold cyan]\n"
        "• [bold green]Uninstall:[/bold green] [bold cyan]neurosort service uninstall[/bold cyan]"
    )
    console.print(Panel(panel_text, title="🤖 24/7 Automation Active", border_style="cyan"))


@service_app.command("status")
def cmd_service_status() -> None:
    """Check the status of the background NeuroSort systemd service."""
    if not shutil.which("systemctl"):
        console.print("[bold red]Error: systemctl not available.[/bold red]")
        raise typer.Exit(1)
    subprocess.run(["systemctl", "--user", "status", "neurosort.service"])


@service_app.command("logs")
def cmd_service_logs(
    lines: int = typer.Option(30, "--lines", "-n", help="Number of journal log lines to display."),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow live output stream."),
) -> None:
    """View recent logs from the background NeuroSort daemon."""
    if not shutil.which("journalctl"):
        console.print("[bold red]Error: journalctl not available.[/bold red]")
        raise typer.Exit(1)
    cmd = ["journalctl", "--user", "-u", "neurosort.service", f"-n{lines}", "--no-pager"]
    if follow:
        cmd = ["journalctl", "--user", "-u", "neurosort.service", "-f"]
    subprocess.run(cmd)


@service_app.command("start")
def cmd_service_start() -> None:
    """Start the background NeuroSort systemd service."""
    subprocess.run(["systemctl", "--user", "start", "neurosort.service"], check=True)
    console.print("[bold green]✓ Started NeuroSort background service.[/bold green]")


@service_app.command("stop")
def cmd_service_stop() -> None:
    """Stop the background NeuroSort systemd service."""
    subprocess.run(["systemctl", "--user", "stop", "neurosort.service"], check=True)
    console.print("[yellow]Stopped NeuroSort background service.[/yellow]")


@service_app.command("restart")
def cmd_service_restart() -> None:
    """Restart the background NeuroSort systemd service."""
    subprocess.run(["systemctl", "--user", "restart", "neurosort.service"], check=True)
    console.print("[bold green]✓ Restarted NeuroSort background service.[/bold green]")


@service_app.command("uninstall")
def cmd_service_uninstall() -> None:
    """Disable and remove the NeuroSort systemd user service."""
    service_file = _get_systemd_user_dir() / "neurosort.service"
    if shutil.which("systemctl"):
        subprocess.run(["systemctl", "--user", "disable", "--now", "neurosort.service"], stderr=subprocess.DEVNULL)
    if service_file.exists():
        service_file.unlink()
    if shutil.which("systemctl"):
        subprocess.run(["systemctl", "--user", "daemon-reload"], stderr=subprocess.DEVNULL)
    console.print("[bold yellow]✓ NeuroSort background service uninstalled cleanly.[/bold yellow]")


if __name__ == "__main__":
    app()
