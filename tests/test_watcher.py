"""Tests for debounced watcher and multi-stage stabilization checker."""

from pathlib import Path
import time
import pytest

from neurosort.config import get_default_config
from neurosort.daemon.watcher import DebouncedEventHandler, StabilizationChecker


def test_stabilization_checker_stable_file(tmp_path: Path):
    checker = StabilizationChecker(check_interval=0.1, max_retries=3)
    test_file = tmp_path / "complete_download.pdf"
    test_file.write_text("Stable PDF content across intervals", encoding="utf-8")

    assert checker.wait_until_stable(test_file) is True


def test_stabilization_checker_empty_file(tmp_path: Path):
    checker = StabilizationChecker(check_interval=0.05, max_retries=2)
    empty_file = tmp_path / "empty.tmp"
    empty_file.touch()

    # Empty file should fail stabilization
    assert checker.wait_until_stable(empty_file) is False


def test_watcher_ignores_temporary_files(tmp_path: Path):
    cfg = get_default_config()
    ready_files = []

    handler = DebouncedEventHandler(
        config=cfg,
        on_file_ready=lambda p: ready_files.append(p),
        debounce_seconds=0.1,
    )

    assert handler._is_ignored(Path("file.crdownload")) is True
    assert handler._is_ignored(Path("document.tmp")) is True
    assert handler._is_ignored(Path("~temp_doc.docx")) is True
    assert handler._is_ignored(Path(".hidden_state")) is True
    assert handler._is_ignored(Path("valid_invoice.pdf")) is False

    handler.shutdown()
