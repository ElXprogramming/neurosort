"""Tests for SQLite transaction ledger operations."""

from datetime import datetime
from pathlib import Path
import pytest

from neurosort.core.ledger import TransactionLedger


def test_ledger_creation_and_indexes(tmp_path: Path):
    db_file = tmp_path / "test.db"
    ledger = TransactionLedger(db_file)
    assert db_file.exists()

    with ledger._get_connection() as conn:
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='transactions';")
        assert cursor.fetchone() is not None


def test_ledger_record_and_query(tmp_path: Path):
    ledger = TransactionLedger(tmp_path / "ledger.db")

    tx_id = ledger.record_move(
        source_path="/tmp/inbox/test.pdf",
        target_path="/tmp/out/test_organized.pdf",
        file_sha256="abc1234567890def",
        metadata={"category": "Finance", "confidence": 0.95},
        status="MOVED",
    )
    assert tx_id > 0

    record = ledger.get_transaction(tx_id)
    assert record is not None
    assert record.id == tx_id
    assert record.source_path == "/tmp/inbox/test.pdf"
    assert record.target_path == "/tmp/out/test_organized.pdf"
    assert record.file_sha256 == "abc1234567890def"
    assert record.status == "MOVED"
    assert record.metadata.get("category") == "Finance"


def test_ledger_history_and_reverted(tmp_path: Path):
    ledger = TransactionLedger(tmp_path / "ledger.db")

    id1 = ledger.record_move("/a/1.txt", "/b/1.txt", "hash1")
    id2 = ledger.record_move("/a/2.txt", "/b/2.txt", "hash2")

    history = ledger.get_history(limit=10)
    assert len(history) == 2
    # Reverse chronological order
    assert history[0].id == id2
    assert history[1].id == id1

    # Mark reverted
    updated = ledger.mark_reverted(id2)
    assert updated is True

    rec2 = ledger.get_transaction(id2)
    assert rec2.status == "REVERTED"

    # Active moves should now only return id1
    active = ledger.get_last_active_moves(limit=5)
    assert len(active) == 1
    assert active[0].id == id1
