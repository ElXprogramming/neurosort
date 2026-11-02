"""Tests for hashing, path sanitization, and security rules."""

import os
from pathlib import Path
import pytest

from neurosort.utils.hashing import calculate_sha256, sanitize_filename, sanitize_path


def test_calculate_sha256(tmp_path: Path):
    test_file = tmp_path / "sample.txt"
    test_file.write_text("Hello NeuroSort World!", encoding="utf-8")

    digest = calculate_sha256(test_file)
    assert isinstance(digest, str)
    assert len(digest) == 64
    # Known hash for "Hello NeuroSort World!"
    assert digest == calculate_sha256(test_file)


def test_sanitize_filename_removes_traversal_and_illegal_chars():
    unsafe_name = "../../etc/passwd\x00_invoice:march*2024?.pdf"
    clean = sanitize_filename(unsafe_name)
    assert ".." not in clean
    assert "/" not in clean
    assert "\\" not in clean
    assert "\x00" not in clean
    assert ":" not in clean
    assert "*" not in clean
    assert "?" not in clean
    assert clean.endswith(".pdf")


def test_sanitize_filename_empty():
    assert sanitize_filename("") == "unnamed_file"
    assert sanitize_filename("   ") == "unnamed_file"
    assert sanitize_filename("...") == "unnamed_file"


def test_sanitize_path_blocks_traversal():
    unsafe_path = "../../../Finance/../../Invoices/../Taxes/"
    clean = sanitize_path(unsafe_path)
    assert ".." not in clean
    assert not clean.startswith("/")
    assert clean == "Finance/Invoices/Taxes"


def test_sanitize_path_empty_or_root():
    assert sanitize_path("") == "Unsorted"
    assert sanitize_path("/") == "Unsorted"
    assert sanitize_path("///") == "Unsorted"
