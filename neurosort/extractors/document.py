"""Document metadata and text extractor supporting PDF, DOCX, TXT, MD, CSV, JSON."""

from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path
import shutil
from typing import Any, Dict, Tuple
import xml.etree.ElementTree as ET
import zipfile

from neurosort.extractors.generic import read_text_sample


def _ocr_pixmap_fallback(pixmap: Any) -> str:
    """Attempt OCR on a PyMuPDF pixmap using pytesseract if installed and binary exists."""
    try:
        import pytesseract
        from PIL import Image

        if not shutil.which("tesseract"):
            return ""

        img = Image.open(io.BytesIO(pixmap.tobytes("png")))
        text = pytesseract.image_to_string(img)
        return text.strip()
    except Exception:
        return ""


def extract_pdf_data(file_path: Path, max_pages: int = 3) -> Tuple[str, Dict[str, Any]]:
    """Extract text snippet and metadata from PDF files using PyMuPDF (fitz) with OCR fallback.

    Args:
        file_path: Path to PDF file.
        max_pages: Maximum number of pages to inspect (default 3).

    Returns:
        Tuple of (extracted_text, metadata_dict).
    """
    text_chunks = []
    metadata: Dict[str, Any] = {"format": "PDF"}

    try:
        try:
            import pymupdf as fitz
        except ImportError:
            import fitz

        with fitz.open(str(file_path)) as doc:
            metadata["page_count"] = len(doc)
            doc_meta = doc.metadata or {}
            if doc_meta.get("title"):
                metadata["title"] = doc_meta["title"]
            if doc_meta.get("author"):
                metadata["author"] = doc_meta["author"]
            if doc_meta.get("creationDate"):
                metadata["creation_date_raw"] = doc_meta["creationDate"]

            pages_to_check = min(max_pages, len(doc))
            for i in range(pages_to_check):
                page = doc[i]
                page_text = page.get_text()
                if page_text:
                    text_chunks.append(page_text.strip())

            combined_text = "\n\n".join(text_chunks).strip()

            # If text is suspiciously short (< 50 chars), check for scanned/image pages and attempt OCR
            if len(combined_text) < 50 and len(doc) > 0:
                ocr_chunks = []
                for i in range(min(2, len(doc))):
                    page = doc[i]
                    pix = page.get_pixmap(dpi=150)
                    ocr_res = _ocr_pixmap_fallback(pix)
                    if ocr_res:
                        ocr_chunks.append(ocr_res)

                if ocr_chunks:
                    combined_text = "\n\n".join(ocr_chunks).strip()
                    metadata["ocr_applied"] = True
                else:
                    metadata["ocr_needed"] = True

            return combined_text[:3000], metadata

    except Exception as exc:
        metadata["error"] = str(exc)
        return "", metadata


def extract_docx_text(file_path: Path) -> Tuple[str, Dict[str, Any]]:
    """Extract text from DOCX files by reading word/document.xml inside zip container."""
    metadata: Dict[str, Any] = {"format": "DOCX"}
    text_chunks = []

    try:
        with zipfile.ZipFile(file_path, "r") as z:
            if "word/document.xml" in z.namelist():
                xml_content = z.read("word/document.xml")
                tree = ET.fromstring(xml_content)
                # Word XML namespace for text elements: {http://schemas.openxmlformats.org/wordprocessingml/2006/main}t
                for node in tree.iter():
                    if node.tag.endswith("}t") and node.text:
                        text_chunks.append(node.text)

        combined = " ".join(text_chunks).strip()
        metadata["word_count_approx"] = len(combined.split())
        return combined[:3000], metadata
    except Exception as exc:
        metadata["error"] = str(exc)
        return "", metadata


def extract_csv_text(file_path: Path) -> Tuple[str, Dict[str, Any]]:
    """Extract column headers and first rows from CSV data."""
    metadata: Dict[str, Any] = {"format": "CSV"}
    preview_lines = []

    try:
        sample = read_text_sample(file_path, max_bytes=8192)
        reader = csv.reader(io.StringIO(sample))
        rows = list(reader)
        if rows:
            metadata["headers"] = rows[0]
            for row in rows[:6]:
                preview_lines.append(", ".join(row))
        return "\n".join(preview_lines), metadata
    except Exception as exc:
        metadata["error"] = str(exc)
        return read_text_sample(file_path, 2000), metadata


def extract_json_text(file_path: Path) -> Tuple[str, Dict[str, Any]]:
    """Extract top-level keys and structure from JSON files."""
    metadata: Dict[str, Any] = {"format": "JSON"}
    try:
        sample = read_text_sample(file_path, max_bytes=8192)
        parsed = json.loads(sample)
        if isinstance(parsed, dict):
            metadata["keys"] = list(parsed.keys())[:15]
            summary = f"JSON Object with keys: {list(parsed.keys())[:10]}"
        elif isinstance(parsed, list):
            metadata["item_count_approx"] = len(parsed)
            summary = f"JSON Array with {len(parsed)} items"
        else:
            summary = str(parsed)[:500]
        return summary, metadata
    except Exception:
        return read_text_sample(file_path, 2000), metadata


def extract_document_context(file_path: Path) -> Tuple[str, Dict[str, Any]]:
    """Dispatch document extraction based on extension."""
    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        return extract_pdf_data(file_path)
    elif suffix in (".docx", ".doc"):
        return extract_docx_text(file_path)
    elif suffix == ".csv":
        return extract_csv_text(file_path)
    elif suffix in (".json", ".jsonl"):
        return extract_json_text(file_path)
    elif suffix in (".txt", ".md", ".markdown", ".rtf", ".log", ".rst"):
        sample = read_text_sample(file_path, max_bytes=4096)
        metadata = {"format": suffix.lstrip(".").upper(), "char_count": len(sample)}
        return sample[:3000], metadata
    else:
        sample = read_text_sample(file_path, max_bytes=2048)
        return sample[:2000], {"format": "PLAINTEXT"}
