"""Tests for document, image, code, and generic file extractors."""

import io
from pathlib import Path
from PIL import Image
import piexif
try:
    import pymupdf as fitz
except ImportError:
    import fitz
import pytest

from neurosort.extractors import extract_file_context
from neurosort.extractors.generic import detect_mime_type


def test_extract_text_document(tmp_path: Path):
    txt_file = tmp_path / "invoice_sample.txt"
    txt_file.write_text(
        "INVOICE #1024\nVendor: Acme Cloud Services\nTotal Amount: $499.00\nPayment due in 30 days.",
        encoding="utf-8",
    )

    ctx = extract_file_context(txt_file)
    assert ctx.file_name == "invoice_sample.txt"
    assert ctx.extension == ".txt"
    assert "Acme Cloud Services" in ctx.extracted_text_snippet
    assert ctx.file_size > 0


def test_extract_csv_document(tmp_path: Path):
    csv_file = tmp_path / "users_dataset.csv"
    csv_file.write_text("id,name,role,email\n1,Alice,Admin,alice@example.com\n2,Bob,User,bob@example.com", encoding="utf-8")

    ctx = extract_file_context(csv_file)
    assert ctx.extension == ".csv"
    assert "id, name, role, email" in ctx.extracted_text_snippet
    assert ctx.raw_metadata.get("headers") == ["id", "name", "role", "email"]


def test_extract_pdf_document(tmp_path: Path):
    pdf_file = tmp_path / "test_report.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 72), "NeuroSort Architecture Design Specification Report - Confidential")
    doc.save(str(pdf_file))
    doc.close()

    ctx = extract_file_context(pdf_file)
    assert ctx.mime_type == "application/pdf"
    assert "NeuroSort Architecture" in ctx.extracted_text_snippet
    assert ctx.raw_metadata.get("page_count") == 1


def test_extract_image_with_exif(tmp_path: Path):
    img_file = tmp_path / "photo_sample.jpg"

    # Create an RGB image
    img = Image.new("RGB", (640, 480), color=(73, 109, 137))

    # Construct minimal EXIF
    zeroth_ifd = {
        piexif.ImageIFD.Make: b"NeuroCam",
        piexif.ImageIFD.Model: b"X100",
    }
    exif_ifd = {
        piexif.ExifIFD.DateTimeOriginal: b"2024:05:20 14:30:00",
    }
    exif_dict = {"0th": zeroth_ifd, "Exif": exif_ifd, "GPS": {}, "1st": {}, "thumbnail": None}
    exif_bytes = piexif.dump(exif_dict)

    img.save(img_file, "jpeg", exif=exif_bytes)

    ctx = extract_file_context(img_file)
    assert ctx.mime_type in ("image/jpeg", "image/jpg")
    assert ctx.raw_metadata.get("width") == 640
    assert ctx.raw_metadata.get("height") == 480
    assert ctx.raw_metadata.get("camera_make") == "NeuroCam"
    assert ctx.raw_metadata.get("camera_model") == "X100"
    assert ctx.created_date.year == 2024
    assert ctx.created_date.month == 5


def test_extract_source_code(tmp_path: Path):
    code_file = tmp_path / "server.py"
    code_file.write_text(
        '"""FastAPI Web Server Daemon for File Orchestration."""\nimport os\nfrom pathlib import Path\n\ndef start():\n    pass\n',
        encoding="utf-8",
    )

    ctx = extract_file_context(code_file)
    assert ctx.raw_metadata.get("language") == "Python"
    assert "FastAPI Web Server Daemon" in ctx.extracted_text_snippet
