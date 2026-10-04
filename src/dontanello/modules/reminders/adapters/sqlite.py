"""SQLite persistence for task digest settings and one-time reminders."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, datetime, time, timezone
from pathlib import Path

from ..models import PersonalReminder, TaskDigestSettings


class SQLiteReminderRepository:
    def __init__(self, path: Path):
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        os.chmod(self.path, 0o600)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            connection.close()
            raise RuntimeError("Unsupported reminder database schema version")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS task_digest_settings (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
                weekdays TEXT NOT NULL,
                send_time TEXT NOT NULL,
                days_ahead INTEGER NOT NULL CHECK (days_ahead BETWEEN 0 AND 365)
            );
            CREATE TABLE IF NOT EXISTS task_digest (
                local_day TEXT PRIMARY KEY,
                status TEXT NOT NULL CHECK (status IN ('pending', 'sending', 'sent', 'empty', 'uncertain')),
                text TEXT NOT NULL,
                next_attempt TEXT
            );
            CREATE TABLE IF NOT EXISTS personal_reminder (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_update_id INTEGER NOT NULL UNIQUE,
                text TEXT NOT NULL,
                due_at TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('pending', 'sending', 'sent', 'cancelled', 'uncertain')),
                next_attempt TEXT
            );
            CREATE INDEX IF NOT EXISTS personal_reminder_due
                ON personal_reminder(status, due_at, next_attempt);
            """
        )
        connection.execute("PRAGMA user_version = 1")
        return connection

    def digest_settings(self, defaults: TaskDigestSettings) -> TaskDigestSettings:
        connection = self._connect()
        try:
            connection.execute(
                """INSERT OR IGNORE INTO task_digest_settings
                   (singleton, enabled, weekdays, send_time, days_ahead)
                   VALUES (1, ?, ?, ?, ?)""",
                (
                    int(defaults.enabled),
                    json.dumps(defaults.weekdays),
                    defaults.send_time.isoformat(timespec="minutes"),
                    defaults.days_ahead,
                ),
            )
            row = connection.execute(
                "SELECT enabled, weekdays, send_time, days_ahead FROM task_digest_settings WHERE singleton=1"
            ).fetchone()
            return TaskDigestSettings(
                enabled=bool(row["enabled"]),
                weekdays=tuple(int(day) for day in json.loads(row["weekdays"])),
                send_time=time.fromisoformat(row["send_time"]),
                days_ahead=int(row["days_ahead"]),
            )
        finally:
            connection.close()

    def save_digest_settings(self, settings: TaskDigestSettings) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """INSERT INTO task_digest_settings(singleton, enabled, weekdays, send_time, days_ahead)
                   VALUES (1, ?, ?, ?, ?)
                   ON CONFLICT(singleton) DO UPDATE SET enabled=excluded.enabled,
                   weekdays=excluded.weekdays, send_time=excluded.send_time,
                   days_ahead=excluded.days_ahead""",
                (
                    int(settings.enabled),
                    json.dumps(settings.weekdays),
                    settings.send_time.isoformat(timespec="minutes"),
                    settings.days_ahead,
                ),
            )
        finally:
            connection.close()

    def digest(self, day: date) -> tuple[str, str, datetime | None] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT status, text, next_attempt FROM task_digest WHERE local_day=?",
                (day.isoformat(),),
            ).fetchone()
            if row is None:
                return None
            next_attempt = (
                datetime.fromisoformat(row["next_attempt"]) if row["next_attempt"] else None
            )
            return row["status"], row["text"], next_attempt
        finally:
            connection.close()

    def prepare_digest(self, day: date, text: str, now: datetime) -> None:
        connection = self._connect()
        try:
            connection.execute(
                "INSERT OR IGNORE INTO task_digest(local_day, status, text) VALUES (?, 'pending', ?)",
                (day.isoformat(), text),
            )
        finally:
            connection.close()

    def skip_digest(self, day: date) -> None:
        connection = self._connect()
        try:
            connection.execute(
                "INSERT OR IGNORE INTO task_digest(local_day, status, text) VALUES (?, 'empty', '')",
                (day.isoformat(),),
            )
        finally:
            connection.close()

    def claim_digest(self, day: date, now: datetime) -> bool:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            result = connection.execute(
                """UPDATE task_digest SET status='sending'
                   WHERE local_day=? AND status='pending'
                   AND (next_attempt IS NULL OR next_attempt <= ?)""",
                (day.isoformat(), _stamp(now)),
            )
            connection.commit()
            return result.rowcount == 1
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def finish_digest(self, day: date, status: str, next_attempt: datetime | None = None) -> None:
        connection = self._connect()
        try:
            connection.execute(
                "UPDATE task_digest SET status=?, next_attempt=? WHERE local_day=? AND status='sending'",
                (status, _stamp(next_attempt) if next_attempt else None, day.isoformat()),
            )
        finally:
            connection.close()

    def create_personal(
        self, update_id: int, text: str, due_at: datetime, created_at: datetime
    ) -> PersonalReminder:
        connection = self._connect()
        try:
            connection.execute(
                "INSERT OR IGNORE INTO personal_reminder(source_update_id, text, due_at, status) VALUES (?, ?, ?, 'pending')",
                (update_id, text, _stamp(due_at)),
            )
            row = connection.execute(
                "SELECT id, source_update_id, text, due_at, status FROM personal_reminder WHERE source_update_id=?",
                (update_id,),
            ).fetchone()
            return _reminder(row)
        finally:
            connection.close()

    def pending_personal(self, limit: int = 20) -> tuple[PersonalReminder, ...]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """SELECT id, source_update_id, text, due_at, status FROM personal_reminder
                   WHERE status IN ('pending', 'uncertain') ORDER BY due_at LIMIT ?""",
                (limit,),
            ).fetchall()
            return tuple(_reminder(row) for row in rows)
        finally:
            connection.close()

    def cancel_personal(self, reminder_id: int) -> bool:
        connection = self._connect()
        try:
            result = connection.execute(
                "UPDATE personal_reminder SET status='cancelled' WHERE id=? AND status='pending'",
                (reminder_id,),
            )
            return result.rowcount == 1
        finally:
            connection.close()

    def claim_due_personal(self, now: datetime) -> PersonalReminder | None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT id, source_update_id, text, due_at, status FROM personal_reminder
                   WHERE status='pending' AND due_at <= ?
                   AND (next_attempt IS NULL OR next_attempt <= ?)
                   ORDER BY due_at, id LIMIT 1""",
                (_stamp(now), _stamp(now)),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            changed = connection.execute(
                "UPDATE personal_reminder SET status='sending' WHERE id=? AND status='pending'",
                (row["id"],),
            )
            connection.commit()
            return _reminder(row) if changed.rowcount == 1 else None
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def finish_personal(
        self, reminder_id: int, status: str, next_attempt: datetime | None = None
    ) -> None:
        connection = self._connect()
        try:
            connection.execute(
                "UPDATE personal_reminder SET status=?, next_attempt=? WHERE id=? AND status='sending'",
                (status, _stamp(next_attempt) if next_attempt else None, reminder_id),
            )
        finally:
            connection.close()

    def recover_inflight(self) -> None:
        connection = self._connect()
        try:
            connection.execute(
                "UPDATE personal_reminder SET status='uncertain' WHERE status='sending'"
            )
            connection.execute("UPDATE task_digest SET status='uncertain' WHERE status='sending'")
        finally:
            connection.close()


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _reminder(row: sqlite3.Row) -> PersonalReminder:
    due_at = datetime.fromisoformat(row["due_at"])
    return PersonalReminder(
        id=int(row["id"]),
        source_update_id=int(row["source_update_id"]),
        text=str(row["text"]),
        due_at=due_at,
        status=str(row["status"]),
    )
