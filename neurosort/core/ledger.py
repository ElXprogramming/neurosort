"""SQLite persistence and audit transaction ledger for NeuroSort."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Dict, Generator, List, Optional
from pydantic import BaseModel, Field


class TransactionRecord(BaseModel):
    """Represents a recorded file movement transaction."""

    id: int
    timestamp: datetime
    source_path: str
    target_path: str
    file_sha256: str
    metadata_json: str = "{}"
    status: str = "MOVED"  # 'MOVED', 'REVERTED', 'FAILED'

    @property
    def metadata(self) -> Dict[str, Any]:
        """Parsed metadata dictionary."""
        try:
            return json.loads(self.metadata_json)
        except Exception:
            return {}


class TransactionLedger:
    """Thread-safe SQLite transaction ledger with WAL mode and audit tracking."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(os.path.expanduser(str(db_path))).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Provide a contextual connection with WAL mode and foreign keys enabled."""
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        )
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Create transaction tables and indexes if they do not exist."""
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    source_path TEXT NOT NULL,
                    target_path TEXT NOT NULL,
                    file_sha256 TEXT NOT NULL,
                    metadata_json TEXT DEFAULT '{}',
                    status TEXT CHECK(status IN ('MOVED', 'REVERTED', 'FAILED')) DEFAULT 'MOVED'
                );
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_transactions_status
                ON transactions(status);
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_transactions_timestamp
                ON transactions(timestamp DESC);
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_transactions_sha256
                ON transactions(file_sha256);
                """
            )

    def record_move(
        self,
        source_path: Path | str,
        target_path: Path | str,
        file_sha256: str,
        metadata: Optional[Dict[str, Any]] = None,
        status: str = "MOVED",
    ) -> int:
        """Record a newly executed file movement in the ledger.

        Args:
            source_path: Original location.
            target_path: New destination.
            file_sha256: SHA-256 hash of file content.
            metadata: Extracted metadata, classification result, tags, confidence.
            status: Initial status ('MOVED' or 'FAILED').

        Returns:
            The newly inserted transaction ID.
        """
        meta_str = json.dumps(metadata or {}, default=str)
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO transactions (source_path, target_path, file_sha256, metadata_json, status)
                VALUES (?, ?, ?, ?, ?)
                """,
                (str(source_path), str(target_path), file_sha256, meta_str, status),
            )
            return cursor.lastrowid or 0

    def mark_reverted(self, tx_id: int) -> bool:
        """Update transaction status to 'REVERTED'.

        Returns:
            True if row was updated, False if transaction not found.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE transactions SET status = 'REVERTED' WHERE id = ?",
                (tx_id,),
            )
            return cursor.rowcount > 0

    def get_transaction(self, tx_id: int) -> Optional[TransactionRecord]:
        """Fetch a single transaction by ID."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT id, timestamp, source_path, target_path, file_sha256, metadata_json, status "
                "FROM transactions WHERE id = ?",
                (tx_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_record(row)

    def get_last_active_moves(self, limit: int = 1) -> List[TransactionRecord]:
        """Get the most recent non-reverted ('MOVED') transactions for rollback."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT id, timestamp, source_path, target_path, file_sha256, metadata_json, status "
                "FROM transactions WHERE status = 'MOVED' ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            rows = cursor.fetchall()
            return [self._row_to_record(r) for r in rows]

    def get_history(self, limit: int = 50) -> List[TransactionRecord]:
        """Retrieve recent transactions in reverse chronological order."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT id, timestamp, source_path, target_path, file_sha256, metadata_json, status "
                "FROM transactions ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            rows = cursor.fetchall()
            return [self._row_to_record(r) for r in rows]

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> TransactionRecord:
        raw_ts = row["timestamp"]
        if isinstance(raw_ts, str):
            try:
                ts = datetime.fromisoformat(raw_ts)
            except Exception:
                ts = datetime.now()
        elif isinstance(raw_ts, datetime):
            ts = raw_ts
        else:
            ts = datetime.now()

        return TransactionRecord(
            id=row["id"],
            timestamp=ts,
            source_path=row["source_path"],
            target_path=row["target_path"],
            file_sha256=row["file_sha256"],
            metadata_json=row["metadata_json"] or "{}",
            status=row["status"],
        )
