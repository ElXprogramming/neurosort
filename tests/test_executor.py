"""Tests for SafeExecutor: atomic moves, collision resolution, dry-run, and zero-data-loss."""

from datetime import datetime
from pathlib import Path
import pytest

from neurosort.config import get_default_config
from neurosort.core.classifier import ClassificationResult
from neurosort.core.executor import SafeExecutor
from neurosort.core.ledger import TransactionLedger
from neurosort.extractors.generic import FileContentContext


@pytest.fixture
def test_setup(tmp_path: Path):
    intake_dir = tmp_path / "intake"
    dest_dir = tmp_path / "organized"
    intake_dir.mkdir()
    dest_dir.mkdir()

    cfg = get_default_config()
    cfg.destination_root = dest_dir
    cfg.database_path = tmp_path / "test_ledger.db"

    ledger = TransactionLedger(cfg.database_path)
    executor = SafeExecutor(cfg, ledger=ledger)

    return intake_dir, dest_dir, executor, ledger, cfg


def test_atomic_move_success(test_setup):
    intake_dir, dest_dir, executor, ledger, _ = test_setup

    source_file = intake_dir / "invoice_1.pdf"
    source_file.write_text("Invoice test content", encoding="utf-8")

    ctx = FileContentContext(
        file_path=source_file,
        file_name="invoice_1.pdf",
        extension=".pdf",
        mime_type="application/pdf",
        file_size=source_file.stat().st_size,
        created_date=datetime(2024, 1, 1),
        extracted_text_snippet="Invoice test content",
    )

    classification = ClassificationResult(
        target_folder="Finance/Invoices",
        standardized_name="2024-01-01_invoice_sample.pdf",
        confidence=0.95,
        reasoning="Test",
        tags=["invoice"],
    )

    res = executor.execute_move(ctx, classification, dry_run=False)

    assert res.success is True
    assert not source_file.exists()
    assert res.target_path.exists()
    assert res.target_path.parent == dest_dir / "Finance" / "Invoices"
    assert res.target_path.read_text(encoding="utf-8") == "Invoice test content"
    assert res.tx_id is not None

    # Check ledger record
    rec = ledger.get_transaction(res.tx_id)
    assert rec is not None
    assert rec.status == "MOVED"
    assert rec.target_path == str(res.target_path)


def test_collision_resolution(test_setup):
    intake_dir, dest_dir, executor, ledger, _ = test_setup

    # Pre-occupy destination with an existing file
    existing_dest = dest_dir / "Finance/Invoices" / "2024-01-01_invoice_sample.pdf"
    existing_dest.parent.mkdir(parents=True, exist_ok=True)
    existing_dest.write_text("Existing prior invoice", encoding="utf-8")

    source_file = intake_dir / "invoice_new.pdf"
    source_file.write_text("New distinct invoice content", encoding="utf-8")

    ctx = FileContentContext(
        file_path=source_file,
        file_name="invoice_new.pdf",
        extension=".pdf",
        mime_type="application/pdf",
        file_size=source_file.stat().st_size,
        created_date=datetime(2024, 1, 1),
        extracted_text_snippet="New distinct invoice content",
    )

    classification = ClassificationResult(
        target_folder="Finance/Invoices",
        standardized_name="2024-01-01_invoice_sample.pdf",
        confidence=0.95,
        reasoning="Collision test",
        tags=["invoice"],
    )

    res = executor.execute_move(ctx, classification, dry_run=False)

    assert res.success is True
    assert res.collision_resolved is True
    assert res.target_path.name == "2024-01-01_invoice_sample_v1.pdf"
    assert res.target_path.exists()
    # Prior file remains completely untouched
    assert existing_dest.read_text(encoding="utf-8") == "Existing prior invoice"


def test_dry_run_leaves_disk_untouched(test_setup):
    intake_dir, dest_dir, executor, ledger, _ = test_setup

    source_file = intake_dir / "preview.txt"
    source_file.write_text("Preview content", encoding="utf-8")

    ctx = FileContentContext(
        file_path=source_file,
        file_name="preview.txt",
        extension=".txt",
        mime_type="text/plain",
        file_size=source_file.stat().st_size,
        created_date=datetime.now(),
        extracted_text_snippet="Preview content",
    )

    classification = ClassificationResult(
        target_folder="Documents/Work",
        standardized_name="preview_clean.txt",
        confidence=0.8,
        reasoning="Dry run test",
        tags=["work"],
    )

    res = executor.execute_move(ctx, classification, dry_run=True)

    assert res.success is True
    assert res.dry_run is True
    # Source file still exists in intake
    assert source_file.exists()
    # Target file was NOT created
    assert not res.target_path.exists()
    assert res.tx_id is None


def test_rollback_restores_file(test_setup):
    intake_dir, dest_dir, executor, ledger, _ = test_setup

    source_file = intake_dir / "document_to_undo.txt"
    source_file.write_text("Restore me perfectly", encoding="utf-8")

    ctx = FileContentContext(
        file_path=source_file,
        file_name="document_to_undo.txt",
        extension=".txt",
        mime_type="text/plain",
        file_size=source_file.stat().st_size,
        created_date=datetime.now(),
        extracted_text_snippet="Restore me",
    )

    classification = ClassificationResult(
        target_folder="Documents/Work",
        standardized_name="restorable_doc.txt",
        confidence=0.9,
        reasoning="Undo test",
        tags=["doc"],
    )

    res = executor.execute_move(ctx, classification, dry_run=False)
    assert res.success is True
    assert not source_file.exists()
    assert res.target_path.exists()

    # Rollback
    rollback = executor.rollback_transaction(res.tx_id)
    assert rollback.success is True
    assert source_file.exists()
    assert source_file.read_text(encoding="utf-8") == "Restore me perfectly"
    assert not res.target_path.exists()

    # Status updated in ledger
    rec = ledger.get_transaction(res.tx_id)
    assert rec.status == "REVERTED"
