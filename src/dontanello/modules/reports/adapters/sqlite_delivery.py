"""SQLite journal for report snapshots and their individual message chunks."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Sequence

from ..delivery import DeliveryChunk


class SQLiteDeliveryStore:
    def __init__(self, path: Path):
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2, 3):
            connection.close()
            raise RuntimeError("Unsupported report journal schema version")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS report_delivery (
                delivery_key TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS report_delivery_chunk (
                delivery_key TEXT NOT NULL REFERENCES report_delivery(delivery_key),
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('pending', 'sending', 'sent', 'uncertain')),
                message_id INTEGER,
                next_attempt TEXT,
                parse_mode TEXT NOT NULL DEFAULT '',
                reply_markup TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (delivery_key, chunk_index)
            );
            CREATE INDEX IF NOT EXISTS report_chunk_status
                ON report_delivery_chunk(status, next_attempt);
            CREATE TABLE IF NOT EXISTS report_metadata (
                name TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(report_delivery_chunk)").fetchall()
        }
        if "parse_mode" not in columns:
            connection.execute(
                "ALTER TABLE report_delivery_chunk ADD COLUMN parse_mode TEXT NOT NULL DEFAULT ''"
            )
        if "reply_markup" not in columns:
            connection.execute(
                "ALTER TABLE report_delivery_chunk ADD COLUMN reply_markup TEXT NOT NULL DEFAULT ''"
            )
        connection.execute("PRAGMA user_version = 3")
        return connection

    @staticmethod
    def _stamp(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _datetime(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value is not None else None

    def prepare(
        self,
        key: str,
        chunks: Sequence[str],
        now: datetime,
        parse_mode: str | None = None,
        reply_markup: dict[str, object] | None = None,
    ) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO report_delivery(delivery_key, created_at) VALUES (?, ?)",
                (key, self._stamp(now)),
            )
            exists = connection.execute(
                "SELECT 1 FROM report_delivery_chunk WHERE delivery_key = ? LIMIT 1", (key,)
            ).fetchone()
            if exists is None:
                connection.executemany(
                    """INSERT INTO report_delivery_chunk
                       (delivery_key, chunk_index, text, status, parse_mode, reply_markup)
                       VALUES (?, ?, ?, 'pending', ?, ?)""",
                    [
                        (
                            key,
                            index,
                            text,
                            parse_mode or "",
                            json.dumps(reply_markup, separators=(",", ":")) if reply_markup else "",
                        )
                        for index, text in enumerate(chunks)
                    ],
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def chunks(self, key: str) -> tuple[DeliveryChunk, ...]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """SELECT chunk_index, text, status, message_id, next_attempt, parse_mode, reply_markup
                   FROM report_delivery_chunk WHERE delivery_key = ? ORDER BY chunk_index""",
                (key,),
            ).fetchall()
            return tuple(
                DeliveryChunk(
                    index=row["chunk_index"],
                    text=row["text"],
                    status=row["status"],
                    message_id=row["message_id"],
                    next_attempt=self._datetime(row["next_attempt"]),
                    parse_mode=row["parse_mode"] or None,
                    reply_markup=json.loads(row["reply_markup"]) if row["reply_markup"] else None,
                )
                for row in rows
            )
        finally:
            connection.close()

    def claim_chunk(self, key: str, index: int, now: datetime) -> bool:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE report_delivery_chunk SET status = 'sending'
                   WHERE delivery_key = ? AND chunk_index = ? AND status = 'pending'
                   AND (next_attempt IS NULL OR datetime(next_attempt) <= datetime(?))""",
                (key, index, self._stamp(now)),
            )
            connection.commit()
            return cursor.rowcount == 1
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def mark_sent(self, key: str, index: int, message_id: int, now: datetime) -> None:
        self._update_chunk(
            key,
            index,
            """UPDATE report_delivery_chunk SET status = 'sent', message_id = ?, next_attempt = NULL
               WHERE delivery_key = ? AND chunk_index = ? AND status = 'sending'""",
            (message_id, key, index),
        )

    def mark_pending(self, key: str, index: int, next_attempt: datetime) -> None:
        self._update_chunk(
            key,
            index,
            """UPDATE report_delivery_chunk SET status = 'pending', next_attempt = ?
               WHERE delivery_key = ? AND chunk_index = ? AND status = 'sending'""",
            (self._stamp(next_attempt), key, index),
        )

    def mark_uncertain(self, key: str, index: int) -> None:
        self._update_chunk(
            key,
            index,
            """UPDATE report_delivery_chunk SET status = 'uncertain', next_attempt = NULL
               WHERE delivery_key = ? AND chunk_index = ? AND status = 'sending'""",
            (key, index),
        )

    def _update_chunk(
        self, key: str, index: int, query: str, parameters: tuple[object, ...]
    ) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(query, parameters)
            if cursor.rowcount != 1:
                raise RuntimeError("delivery chunk is not in the expected sending state")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def recover_stale_sending(self, key: str) -> None:
        """A chunk left sending by a crash has an unknowable transport outcome."""
        connection = self._connect()
        try:
            connection.execute(
                """UPDATE report_delivery_chunk SET status = 'uncertain', next_attempt = NULL
                   WHERE delivery_key = ? AND status = 'sending'""",
                (key,),
            )
        finally:
            connection.close()

    def active_since(self, now: datetime) -> date:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT value FROM report_metadata WHERE name = 'active_since'"
            ).fetchone()
            if row is None:
                value = now.date().isoformat()
                connection.execute(
                    "INSERT INTO report_metadata(name, value) VALUES ('active_since', ?)",
                    (value,),
                )
            else:
                value = row["value"]
            connection.commit()
            return date.fromisoformat(value)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def status(self) -> dict[str, int]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM report_delivery_chunk GROUP BY status"
            ).fetchall()
            result = {"pending": 0, "sending": 0, "sent": 0, "uncertain": 0}
            result.update({row["status"]: row["count"] for row in rows})
            return result
        finally:
            connection.close()
