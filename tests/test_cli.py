"""Tests for NeuroSort CLI commands using Typer CliRunner."""

from pathlib import Path
import pytest
from typer.testing import CliRunner

from neurosort.cli import app
from neurosort.config import get_default_config, save_config

runner = CliRunner()


def test_cli_init_command(tmp_path: Path):
    target_cfg = tmp_path / "custom_config.yaml"
    result = runner.invoke(app, ["init", "--path", str(target_cfg), "--force"])
    assert result.exit_code == 0
    assert "Successfully created configuration" in result.output
    assert target_cfg.exists()


def test_cli_scan_dry_run(tmp_path: Path):
    intake = tmp_path / "intake"
    intake.mkdir()
    (intake / "sample_invoice.txt").write_text("Invoice from Stripe $99", encoding="utf-8")

    cfg_file = tmp_path / "config.yaml"
    cfg = get_default_config()
    cfg.destination_root = tmp_path / "organized"
    cfg.database_path = tmp_path / "ledger.db"
    save_config(cfg, cfg_file)

    result = runner.invoke(
        app, ["scan", str(intake), "--dry-run", "--config", str(cfg_file)]
    )
    assert result.exit_code == 0
    assert "DRY RUN PREVIEW" in result.output
    assert "sample_invoice.txt" in result.output
    # File still remains in intake
    assert (intake / "sample_invoice.txt").exists()


def test_cli_scan_execute_and_history_and_undo(tmp_path: Path):
    intake = tmp_path / "intake"
    intake.mkdir()
    test_doc = intake / "quarterly_report.txt"
    test_doc.write_text("Company quarterly project report and overview", encoding="utf-8")

    cfg_file = tmp_path / "config.yaml"
    cfg = get_default_config()
    cfg.destination_root = tmp_path / "organized"
    cfg.database_path = tmp_path / "ledger.db"
    save_config(cfg, cfg_file)

    # 1. Execute Scan
    scan_res = runner.invoke(app, ["scan", str(intake), "--config", str(cfg_file)])
    assert scan_res.exit_code == 0
    assert "Successfully organized" in scan_res.output
    assert not test_doc.exists()

    # 2. Check History
    hist_res = runner.invoke(app, ["history", "--config", str(cfg_file)])
    assert hist_res.exit_code == 0
    clean_hist = hist_res.output.replace("\n", "").replace(" ", "")
    assert "quarterly_report.txt" in clean_hist

    # 3. Execute Undo
    undo_res = runner.invoke(app, ["undo", "--last", "1", "--config", str(cfg_file)])
    assert undo_res.exit_code == 0
    assert "REVERTED" in undo_res.output
    # Original file restored to intake!
    assert test_doc.exists()


def test_cli_undo_all(tmp_path: Path):
    intake = tmp_path / "intake"
    intake.mkdir()
    doc1 = intake / "file1.txt"
    doc2 = intake / "file2.txt"
    doc1.write_text("File 1 content", encoding="utf-8")
    doc2.write_text("File 2 content", encoding="utf-8")

    cfg_file = tmp_path / "config.yaml"
    cfg = get_default_config()
    cfg.destination_root = tmp_path / "organized"
    cfg.database_path = tmp_path / "ledger.db"
    save_config(cfg, cfg_file)

    runner.invoke(app, ["scan", str(intake), "--config", str(cfg_file)])
    assert not doc1.exists()
    assert not doc2.exists()

    undo_all_res = runner.invoke(app, ["undo", "--all", "--config", str(cfg_file)])
    assert undo_all_res.exit_code == 0
    assert doc1.exists()
    assert doc2.exists()
