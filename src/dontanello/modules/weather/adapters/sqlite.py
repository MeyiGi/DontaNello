"""Durable daily weather delivery state."""

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from ..models import DailyDelivery


class SQLiteWeatherRepository:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS weather_delivery (
                    local_date TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK (status IN ('pending','sending','sent','uncertain')),
                    text TEXT,
                    next_attempt TEXT,
                    updated_at TEXT NOT NULL
                )"""
            )
        os.chmod(self.path, 0o600)

    def get_daily(self, day: date) -> DailyDelivery | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status, text, next_attempt FROM weather_delivery WHERE local_date = ?",
                (day.isoformat(),),
            ).fetchone()
        if row is None:
            return None
        next_attempt = datetime.fromisoformat(row[2]) if row[2] else None
        return DailyDelivery(row[0], row[1], next_attempt)

    def prepare_daily(self, day: date, now: datetime) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO weather_delivery(local_date,status,updated_at) VALUES(?, 'pending', ?)",
                (day.isoformat(), now.isoformat()),
            )

    def save_daily_text(self, day: date, text: str, now: datetime) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE weather_delivery SET text = ?, updated_at = ? WHERE local_date = ? AND status = 'pending'",
                (text, now.isoformat(), day.isoformat()),
            )

    def claim_daily(self, day: date, now: datetime) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE weather_delivery SET status = 'sending', updated_at = ?
                   WHERE local_date = ? AND status = 'pending'
                   AND (next_attempt IS NULL OR next_attempt <= ?)""",
                (now.isoformat(), day.isoformat(), now.isoformat()),
            )
            return cursor.rowcount == 1

    def finish_daily(
        self, day: date, status: str, now: datetime, next_attempt: datetime | None = None
    ) -> None:
        if status not in {"pending", "sent", "uncertain"}:
            raise ValueError("Unsupported weather delivery state")
        with self._connect() as connection:
            connection.execute(
                """UPDATE weather_delivery SET status = ?, next_attempt = ?, updated_at = ?
                   WHERE local_date = ?""",
                (
                    status,
                    next_attempt.isoformat() if next_attempt else None,
                    now.isoformat(),
                    day.isoformat(),
                ),
            )

    def recover_inflight(self) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE weather_delivery SET status = 'uncertain' WHERE status = 'sending'"
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
