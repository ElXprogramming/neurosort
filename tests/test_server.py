"""Tests for local web GUI REST API server."""

from pathlib import Path
import pytest

from neurosort.config import get_default_config, save_config
from neurosort.server import NeuroSortServerState


def test_server_state_lifecycle(tmp_path: Path):
    cfg_file = tmp_path / "config.yaml"
    cfg = get_default_config()
    cfg.destination_root = tmp_path / "organized"
    cfg.database_path = tmp_path / "ledger.db"
    save_config(cfg, cfg_file)

    state = NeuroSortServerState(cfg_file)
    assert not state.is_daemon_running()

    # Can start and stop daemon safely
    state.start_daemon()
    assert state.is_daemon_running()

    state.stop_daemon()
    assert not state.is_daemon_running()
