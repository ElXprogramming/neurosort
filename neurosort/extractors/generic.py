"""Generic file inspector, MIME detection, and unified FileContentContext schema."""

from __future__ import annotations

from datetime import datetime
import mimetypes
import os
from pathlib import Path
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class FileContentContext(BaseModel):
    """Unified context object extracted from any inspected file."""

    file_path: Path
    file_name: str
    extension: str
    mime_type: str
    file_size: int
    created_date: datetime
    extracted_text_snippet: str = ""
    raw_metadata: Dict[str, Any] = Field(default_factory=dict)

    @property
    def extension_normalized(self) -> str:
        """Lowercase extension without leading dot."""
        ext = self.extension.lower()
        return ext[1:] if ext.startswith(".") else ext


def detect_mime_type(file_path: Path | str) -> str:
    """Detect MIME type using python-magic with fallback to standard mimetypes and byte inspection.

    Args:
        file_path: Path to target file.

    Returns:
        MIME type string, e.g., 'application/pdf', 'image/jpeg', 'text/plain'.
    """
    path = Path(file_path)

    # 1. Try python-magic if available
    try:
        import magic
        # Use from_file if available
        if hasattr(magic, "from_file"):
            mime = magic.from_file(str(path), mime=True)
            if mime and isinstance(mime, str):
                return mime
        elif hasattr(magic, "Magic"):
            m = magic.Magic(mime=True)
            mime = m.from_file(str(path))
            if mime and isinstance(mime, str):
                return mime
    except Exception:
        pass

    # 2. Try standard mimetypes library based on filename
    guess, _ = mimetypes.guess_type(str(path))
    if guess:
        return guess

    # 3. Fast magic byte signature fallback
    try:
        with path.open("rb") as f:
            header = f.read(16)
            if header.startswith(b"%PDF"):
                return "application/pdf"
            if header.startswith(b"\x89PNG\r\n\x1a\n"):
                return "image/png"
            if header.startswith(b"\xff\xd8\xff"):
                return "image/jpeg"
            if header.startswith(b"GIF87a") or header.startswith(b"GIF89a"):
                return "image/gif"
            if header.startswith(b"PK\x03\x04"):
                return "application/zip"
            if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
                return "image/webp"
    except Exception:
        pass

    return "application/octet-stream"


def read_text_sample(file_path: Path | str, max_bytes: int = 8192) -> str:
    """Read initial bytes of a text file safely with multiple encoding fallbacks.

    Args:
        file_path: Path to text file.
        max_bytes: Maximum number of bytes to sample.

    Returns:
        Decoded text snippet.
    """
    path = Path(file_path)
    try:
        with path.open("rb") as f:
            chunk = f.read(max_bytes)
    except Exception:
        return ""

    for encoding in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
        try:
            return chunk.decode(encoding)
        except UnicodeDecodeError:
            continue

    return chunk.decode("utf-8", errors="replace")
