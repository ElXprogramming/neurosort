"""Tests for classification engine, rule precedence, and sanitization."""

from datetime import datetime
from pathlib import Path
import pytest

from neurosort.config import get_default_config
from neurosort.core.classifier import ClassificationResult, SemanticClassifier
from neurosort.extractors.generic import FileContentContext


@pytest.fixture
def classifier() -> SemanticClassifier:
    cfg = get_default_config()
    return SemanticClassifier(cfg)


def test_exact_override_precedence(classifier: SemanticClassifier, tmp_path: Path):
    ctx = FileContentContext(
        file_path=tmp_path / "ubuntu-24.04-desktop.iso",
        file_name="ubuntu-24.04-desktop.iso",
        extension=".iso",
        mime_type="application/x-iso9660-image",
        file_size=1024,
        created_date=datetime(2024, 4, 25),
        extracted_text_snippet="",
        raw_metadata={},
    )

    result = classifier.classify(ctx)
    assert result.target_folder == "Archives/ISO"
    assert result.confidence == 1.0
    assert "override" in result.tags


def test_heuristic_invoice_classification(classifier: SemanticClassifier, tmp_path: Path):
    ctx = FileContentContext(
        file_path=tmp_path / "statement_march.pdf",
        file_name="statement_march.pdf",
        extension=".pdf",
        mime_type="application/pdf",
        file_size=5000,
        created_date=datetime(2024, 3, 15),
        extracted_text_snippet="INVOICE BILLING STATEMENT - Vendor: Stripe Payment Services. Total: $50.00",
        raw_metadata={},
    )

    result = classifier.classify(ctx)
    assert result.target_folder == "Finance/Invoices"
    assert result.confidence >= 0.7
    assert result.standardized_name.endswith(".pdf")
    assert "stripe" in result.standardized_name or "march" in result.standardized_name


def test_heuristic_medical_classification(classifier: SemanticClassifier, tmp_path: Path):
    ctx = FileContentContext(
        file_path=tmp_path / "clinic_report.txt",
        file_name="clinic_report.txt",
        extension=".txt",
        mime_type="text/plain",
        file_size=2000,
        created_date=datetime(2024, 6, 1),
        extracted_text_snippet="Medical lab_result and prescription from doctor blood_test results normal",
        raw_metadata={},
    )

    result = classifier.classify(ctx)
    assert result.target_folder == "Health/Medical"
    assert "medical" in result.tags or "prescription" in result.tags


def test_sanitization_blocks_path_traversal(classifier: SemanticClassifier, tmp_path: Path):
    ctx = FileContentContext(
        file_path=tmp_path / "normal.txt",
        file_name="normal.txt",
        extension=".txt",
        mime_type="text/plain",
        file_size=100,
        created_date=datetime.now(),
        extracted_text_snippet="Sample",
        raw_metadata={},
    )

    unsafe = ClassificationResult(
        target_folder="../../etc/cron.d",
        standardized_name="../../../malicious.sh\x00.txt",
        confidence=0.9,
        reasoning="Test",
        tags=["test"],
    )

    sanitized = classifier._sanitize_result(unsafe, ctx)
    assert ".." not in sanitized.target_folder
    assert not sanitized.target_folder.startswith("/")
    assert ".." not in sanitized.standardized_name
    assert "\x00" not in sanitized.standardized_name
    assert sanitized.standardized_name.endswith(".txt")
