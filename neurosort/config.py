"""Configuration schemas and loader for NeuroSort."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union
import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class WatchPathConfig(BaseModel):
    """Configuration for an individual watched directory."""

    path: Path
    debounce_seconds: float = Field(default=5.0, ge=0.5, le=300.0)

    @field_validator("path", mode="before")
    @classmethod
    def expand_path(cls, v: Any) -> Path:
        if isinstance(v, (str, Path)):
            return Path(os.path.expanduser(str(v))).resolve()
        raise ValueError(f"Invalid path specification: {v}")


class RulesPrecedence(BaseModel):
    """Rule precedence definitions for classification."""

    exact_overrides: Dict[str, str] = Field(
        default_factory=lambda: {
            "*.iso": "Archives/ISO",
            "*.dmg": "Installers/macOS",
            "*.deb": "Installers/Linux",
            "*.rpm": "Installers/Linux",
            "*.torrent": "Downloads/Torrents",
        }
    )
    taxonomy_clusters: Dict[str, List[str]] = Field(
        default_factory=lambda: {
            "Finance/Invoices": [
                "invoice",
                "receipt",
                "billing",
                "tax",
                "statement",
                "w2",
                "1099",
                "payment",
                "expense",
            ],
            "Health/Medical": [
                "prescription",
                "medical",
                "lab_result",
                "vaccine",
                "doctor",
                "blood_test",
                "clinic",
                "hospital",
            ],
            "Dev/Datasets": [
                "dataset",
                "csv",
                "jsonl",
                "parquet",
                "benchmark",
                "corpus",
                "training_data",
            ],
            "Dev/SourceCode": [
                "script",
                "source",
                "module",
                "python",
                "rust",
                "javascript",
                "typescript",
                "golang",
            ],
            "Media/Wallpapers": [
                "wallpaper",
                "background",
                "4k",
                "desktop",
                "ultrawide",
            ],
            "Media/Photos": [
                "dcim",
                "camera",
                "photo",
                "portrait",
                "snapshot",
            ],
            "Documents/Work": [
                "report",
                "contract",
                "nda",
                "proposal",
                "resume",
                "curriculum_vitae",
                "cv",
                "presentation",
            ],
        }
    )


class AIProviderConfig(BaseModel):
    """Configuration for LLM classification inference layer."""

    provider: Literal["ollama", "openai", "mock"] = "ollama"
    model: str = "llama3.2"
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    base_url: str = "http://localhost:11434"
    api_key: Optional[str] = None
    timeout_seconds: float = Field(default=30.0, ge=1.0)
    fallback_to_rules: bool = True


class NeuroSortConfig(BaseModel):
    """Top-level NeuroSort configuration schema."""

    watch_paths: List[WatchPathConfig] = Field(
        default_factory=lambda: [
            WatchPathConfig(path=Path("~/Downloads")),
            WatchPathConfig(path=Path("~/Desktop/Inbox")),
        ]
    )
    destination_root: Path = Field(
        default_factory=lambda: Path(os.path.expanduser("~/Documents/Organized")).resolve()
    )
    rules_precedence: RulesPrecedence = Field(default_factory=RulesPrecedence)
    renaming_template: str = "{date}_{category}_{entity}_{original_title}.{ext}"
    ai_provider: AIProviderConfig = Field(default_factory=AIProviderConfig)
    dry_run: bool = False
    ignore_patterns: List[str] = Field(
        default_factory=lambda: [
            "*.crdownload",
            "*.tmp",
            "~*",
            ".*",
            "*.part",
            "*.swp",
            "*.download",
        ]
    )
    database_path: Path = Field(
        default_factory=lambda: Path(os.path.expanduser("~/.neurosort/ledger.db")).resolve()
    )

    @field_validator("destination_root", "database_path", mode="before")
    @classmethod
    def expand_path_field(cls, v: Any) -> Path:
        if isinstance(v, (str, Path)):
            return Path(os.path.expanduser(str(v))).resolve()
        raise ValueError(f"Invalid path: {v}")

    @model_validator(mode="before")
    @classmethod
    def normalize_watch_paths(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        raw_paths = data.get("watch_paths")
        if isinstance(raw_paths, list):
            normalized = []
            for item in raw_paths:
                if isinstance(item, (str, Path)):
                    normalized.append({"path": str(item), "debounce_seconds": 5.0})
                elif isinstance(item, dict):
                    normalized.append(item)
                else:
                    normalized.append(item)
            data["watch_paths"] = normalized
        return data


DEFAULT_CONFIG_FILENAME = "config.yaml"


def get_default_config() -> NeuroSortConfig:
    """Return a pristine default configuration instance."""
    return NeuroSortConfig()


def find_config_path(explicit_path: Optional[Union[str, Path]] = None) -> Path:
    """Resolve configuration file path from explicit argument, current directory, or user home."""
    if explicit_path:
        path = Path(os.path.expanduser(str(explicit_path))).resolve()
        if path.exists():
            return path
        raise FileNotFoundError(f"Specified configuration file not found: {path}")

    cwd_path = Path.cwd() / DEFAULT_CONFIG_FILENAME
    if cwd_path.exists():
        return cwd_path

    home_path = Path.home() / ".config" / "neurosort" / DEFAULT_CONFIG_FILENAME
    if home_path.exists():
        return home_path

    # Return local default path even if not yet created
    return cwd_path


def load_config(path: Optional[Union[str, Path]] = None) -> NeuroSortConfig:
    """Load and validate NeuroSort configuration from YAML file.

    If file does not exist and no explicit path was provided, returns default configuration.
    """
    config_file = find_config_path(path) if path else (Path.cwd() / DEFAULT_CONFIG_FILENAME)

    if not config_file.exists():
        return get_default_config()

    with config_file.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    return NeuroSortConfig.model_validate(data)


def save_config(config: NeuroSortConfig, target_path: Union[str, Path]) -> Path:
    """Persist a NeuroSort configuration instance to a YAML file."""
    path = Path(os.path.expanduser(str(target_path))).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = config.model_dump(mode="json")
    # Convert paths to strings for YAML readability
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, indent=2)

    return path
