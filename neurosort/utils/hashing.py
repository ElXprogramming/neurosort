"""Hashing, path sanitization, and filesystem safety utilities."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path, PurePosixPath


def calculate_sha256(file_path: Path | str, chunk_size: int = 65536) -> str:
    """Calculate the SHA-256 hash of a file efficiently in streaming chunks.

    Args:
        file_path: Path to the target file.
        chunk_size: Byte chunk size for reading (default 64KB).

    Returns:
        Hexadecimal string of SHA-256 digest.

    Raises:
        FileNotFoundError: If the file does not exist.
        PermissionError: If the file is inaccessible.
    """
    path = Path(file_path)
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()


def sanitize_filename(name: str, max_length: int = 220) -> str:
    """Sanitize a filename to guarantee safety across POSIX and Windows filesystems.

    Prevents path traversal, null bytes, illegal characters, and leading/trailing dots/spaces.

    Args:
        name: Proposed filename with or without extension.
        max_length: Maximum allowed character length for the filename.

    Returns:
        Sanitized, safe filename.
    """
    if not name:
        return "unnamed_file"

    # Strip null bytes and control characters
    cleaned = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", name.strip())

    # Replace path separators and Windows forbidden characters: / \ : * ? " < > |
    cleaned = re.sub(r'[/\\:\*\?"<>\|\r\n\t]', "-", cleaned)

    # Collapse multiple hyphens or underscores
    cleaned = re.sub(r"-+", "-", cleaned)
    cleaned = re.sub(r"\s+", "_", cleaned)

    # Avoid leading dots or dashes that might hide files or break CLI args
    cleaned = cleaned.lstrip(".-")

    if not cleaned:
        cleaned = "unnamed_file"

    # Separate stem and extension for truncation
    path_obj = Path(cleaned)
    stem = path_obj.stem
    ext = path_obj.suffix

    if len(cleaned) > max_length:
        remaining = max(1, max_length - len(ext))
        cleaned = f"{stem[:remaining]}{ext}"

    return cleaned


def sanitize_path(subpath: str) -> str:
    """Sanitize a relative folder subpath to prevent path traversal attacks.

    Disallows '..', absolute root paths, or drive specifiers.

    Args:
        subpath: Proposed relative path (e.g., 'Finance/Invoices').

    Returns:
        Safe relative POSIX path string.
    """
    if not subpath:
        return "Unsorted"

    # Strip null bytes and backslashes converted to forward slashes
    normalized = subpath.replace("\\", "/").strip()
    normalized = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", normalized)

    # Normalize parts using PurePosixPath
    parts = PurePosixPath(normalized).parts

    safe_parts = []
    for part in parts:
        part_clean = part.strip()
        # Drop traversal tokens and empty parts
        if part_clean in ("", ".", "..", "/"):
            continue
        # Also clean each directory segment
        safe_segment = sanitize_filename(part_clean)
        if safe_segment and safe_segment not in (".", ".."):
            safe_parts.append(safe_segment)

    if not safe_parts:
        return "Unsorted"

    return "/".join(safe_parts)
