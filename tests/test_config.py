"""Tests for configuration loading, validation, and serialization."""

from pathlib import Path
import pytest
import yaml

from neurosort.config import (
    NeuroSortConfig,
    WatchPathConfig,
    get_default_config,
    load_config,
    save_config,
)


def test_default_config():
    cfg = get_default_config()
    assert len(cfg.watch_paths) >= 1
    assert cfg.ai_provider.provider in ("ollama", "openai", "mock")
    assert cfg.rules_precedence.exact_overrides.get("*.iso") == "Archives/ISO"
    assert "Finance/Invoices" in cfg.rules_precedence.taxonomy_clusters


def test_save_and_load_config(tmp_path: Path):
    cfg_file = tmp_path / "custom_config.yaml"
    cfg = get_default_config()
    cfg.destination_root = tmp_path / "OrganizedTarget"
    cfg.renaming_template = "{date}_{category}_{original_title}.{ext}"

    saved_path = save_config(cfg, cfg_file)
    assert saved_path.exists()

    loaded = load_config(cfg_file)
    assert loaded.destination_root == (tmp_path / "OrganizedTarget").resolve()
    assert loaded.renaming_template == "{date}_{category}_{original_title}.{ext}"


def test_watch_paths_normalization():
    data = {
        "watch_paths": ["/tmp/intake1", {"path": "/tmp/intake2", "debounce_seconds": 10.0}],
    }
    cfg = NeuroSortConfig.model_validate(data)
    assert len(cfg.watch_paths) == 2
    assert cfg.watch_paths[0].path == Path("/tmp/intake1").resolve()
    assert cfg.watch_paths[0].debounce_seconds == 5.0
    assert cfg.watch_paths[1].path == Path("/tmp/intake2").resolve()
    assert cfg.watch_paths[1].debounce_seconds == 10.0
