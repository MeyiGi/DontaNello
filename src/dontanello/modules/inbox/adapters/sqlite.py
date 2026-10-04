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
        os.chmod(path, 0o600)

    def claim(self, update_id: int, title: str, created_at: str) -> InboxCapture:
        with closing(self._connect()) as database, database:
            database.execute("BEGIN IMMEDIATE")
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

    def _connect(self) -> sqlite3.Connection:
        database = sqlite3.connect(self.path, timeout=10)
        database.execute("PRAGMA busy_timeout=10000")
        return database
