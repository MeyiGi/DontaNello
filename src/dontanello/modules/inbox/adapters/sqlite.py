"""Durable idempotency and uncertain-outcome tracking for Inbox captures."""

import os
import sqlite3
from contextlib import closing
from pathlib import Path

from ..models import InboxCapture


class SQLiteInboxCaptureStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with closing(sqlite3.connect(path, timeout=10)) as database, database:
            database.execute("PRAGMA journal_mode=WAL")
            database.execute("PRAGMA busy_timeout=10000")
            database.execute("""CREATE TABLE IF NOT EXISTS captures (
                update_id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('inflight','created','rejected','uncertain')),
                page_url TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )""")
            database.execute("""CREATE TABLE IF NOT EXISTS pending_prompt (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                expires_at REAL NOT NULL
            )""")
        os.chmod(path, 0o600)

    def claim(self, update_id: int, title: str, created_at: str) -> InboxCapture:
        with closing(self._connect()) as database, database:
            database.execute("BEGIN IMMEDIATE")
            return self._claim(database, update_id, title, created_at)

    def has_capture(self, update_id: int) -> bool:
        with closing(self._connect()) as database:
            return (
                database.execute(
                    "SELECT 1 FROM captures WHERE update_id = ?", (update_id,)
                ).fetchone()
                is not None
            )

    def set_pending_prompt(self, expires_at: float) -> None:
        with closing(self._connect()) as database, database:
            database.execute(
                "INSERT INTO pending_prompt(singleton, expires_at) VALUES (1, ?) "
                "ON CONFLICT(singleton) DO UPDATE SET expires_at = excluded.expires_at",
                (expires_at,),
            )

    def has_pending_prompt(self, now_timestamp: float) -> bool:
        with closing(self._connect()) as database, database:
            row = database.execute(
                "SELECT expires_at FROM pending_prompt WHERE singleton = 1"
            ).fetchone()
            if row is None:
                return False
            if row[0] <= now_timestamp:
                database.execute("DELETE FROM pending_prompt WHERE singleton = 1")
                return False
            return True

    def claim_pending(
        self, update_id: int, title: str, created_at: str, now_timestamp: float
    ) -> InboxCapture | None:
        with closing(self._connect()) as database, database:
            database.execute("BEGIN IMMEDIATE")
            existing = database.execute(
                "SELECT 1 FROM captures WHERE update_id = ?", (update_id,)
            ).fetchone()
            if existing is not None:
                return self._claim(database, update_id, title, created_at)
            row = database.execute(
                "SELECT expires_at FROM pending_prompt WHERE singleton = 1"
            ).fetchone()
            if row is None or row[0] <= now_timestamp:
                if row is not None:
                    database.execute("DELETE FROM pending_prompt WHERE singleton = 1")
                return None
            capture = self._claim(database, update_id, title, created_at)
            database.execute("DELETE FROM pending_prompt WHERE singleton = 1")
            return capture

    def clear_pending_prompt(self) -> None:
        with closing(self._connect()) as database, database:
            database.execute("DELETE FROM pending_prompt WHERE singleton = 1")

    def finish(self, update_id: int, status: str, page_url: str = "") -> None:
        if status not in {"created", "rejected", "uncertain"}:
            raise ValueError("invalid Inbox capture status")
        with closing(self._connect()) as database, database:
            result = database.execute(
                "UPDATE captures SET status = ?, page_url = ? WHERE update_id = ? AND status = 'inflight'",
                (status, page_url, update_id),
            )
            if result.rowcount != 1:
                raise ValueError("Inbox capture is not in flight")

    def recover_inflight(self) -> None:
        with closing(self._connect()) as database, database:
            database.execute("UPDATE captures SET status = 'uncertain' WHERE status = 'inflight'")

    @staticmethod
    def _claim(
        database: sqlite3.Connection, update_id: int, title: str, created_at: str
    ) -> InboxCapture:
        row = database.execute(
            "SELECT title, status, page_url FROM captures WHERE update_id = ?", (update_id,)
        ).fetchone()
        if row is None:
            database.execute(
                "INSERT INTO captures(update_id, title, status, created_at) VALUES (?, ?, 'inflight', ?)",
                (update_id, title, created_at),
            )
            return InboxCapture(update_id, title, "inflight")
        old_title, status, page_url = row
        if old_title != title:
            raise ValueError("Telegram update was already used for another Inbox capture")
        if status == "inflight":
            database.execute(
                "UPDATE captures SET status = 'uncertain' WHERE update_id = ?", (update_id,)
            )
            status = "uncertain"
        return InboxCapture(update_id, title, status, page_url)

    def _connect(self) -> sqlite3.Connection:
        database = sqlite3.connect(self.path, timeout=10)
        database.execute("PRAGMA busy_timeout=10000")
        return database
