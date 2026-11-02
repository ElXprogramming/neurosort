"""Content extractors package for NeuroSort."""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
from typing import Optional

from neurosort.extractors.code import EXTENSION_LANGUAGE_MAP, extract_code_data
from neurosort.extractors.document import extract_document_context
from neurosort.extractors.generic import FileContentContext, detect_mime_type, read_text_sample
from neurosort.extractors.image import extract_image_data

IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".bmp",
    ".tiff",
    ".tif",
    ".svg",
    ".heic",
    ".ico",
}

DOCUMENT_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".doc",
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".rtf",
    ".epub",
}


def extract_file_context(file_path: Path | str) -> FileContentContext:
    """Analyze a file and build a unified FileContentContext model.

    Dispatches to document, image, code, or generic extractors based on MIME type and extension.

    Args:
        file_path: Absolute or relative path to target file.

    Returns:
        Structured FileContentContext containing metadata and extracted text.
    """
    path = Path(file_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    stat = path.stat()
    file_size = stat.st_size
    # Use mtime for created_date fallback
    created_date = datetime.fromtimestamp(stat.st_mtime)

    ext = path.suffix.lower()
    mime_type = detect_mime_type(path)

    text_snippet = ""
    raw_metadata = {}

    if ext in IMAGE_EXTENSIONS or mime_type.startswith("image/"):
        text_snippet, raw_metadata = extract_image_data(path)
        # Use EXIF date if available
        if "exif_datetime" in raw_metadata:
            try:
                # Format commonly 'YYYY:MM:DD HH:MM:SS'
                exif_dt = datetime.strptime(
                    raw_metadata["exif_datetime"][:19], "%Y:%m:%d %H:%M:%S"
                )
                created_date = exif_dt
            except Exception:
                pass

    elif ext in EXTENSION_LANGUAGE_MAP:
        text_snippet, raw_metadata = extract_code_data(path)

    elif ext in DOCUMENT_EXTENSIONS or mime_type.startswith("text/") or mime_type == "application/pdf":
        text_snippet, raw_metadata = extract_document_context(path)

    else:
        # Generic inspection
        sample = read_text_sample(path, max_bytes=1024)
        text_snippet = sample[:1000] if sample else f"Binary file ({mime_type})"
        raw_metadata = {"format": "GENERIC_BINARY"}

    return FileContentContext(
        file_path=path,
        file_name=path.name,
        extension=path.suffix,
        mime_type=mime_type,
        file_size=file_size,
        created_date=created_date,
        extracted_text_snippet=text_snippet,
        raw_metadata=raw_metadata,
    )


__all__ = [
    "FileContentContext",
    "detect_mime_type",
    "extract_file_context",
    "extract_document_context",
    "extract_image_data",
    "extract_code_data",
]
