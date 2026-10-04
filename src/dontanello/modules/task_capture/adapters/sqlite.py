"""Durable, idempotent Telegram task proposals and creation outcomes."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

from ..models import TaskDraft, TaskProposal


class SQLiteTaskCaptureRepository:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with closing(self._connect()) as database, database:
            database.execute("PRAGMA journal_mode=WAL")
            database.execute(
                """CREATE TABLE IF NOT EXISTS task_capture_proposal (
                    proposal_id TEXT PRIMARY KEY,
                    update_id INTEGER NOT NULL UNIQUE,
                    request_text TEXT NOT NULL,
                    title TEXT NOT NULL,
                    due_date TEXT,
                    status TEXT NOT NULL CHECK(status IN
                        ('pending','creating','created','cancelled','uncertain','rejected')),
                    page_url TEXT NOT NULL DEFAULT ''
                )"""
            )
        os.chmod(path, 0o600)

    def proposal_for_update(self, update_id: int) -> TaskProposal | None:
        with closing(self._connect()) as database:
            row = database.execute(
                "SELECT * FROM task_capture_proposal WHERE update_id = ?", (update_id,)
            ).fetchone()
        return self._model(row)

    def get_proposal(self, proposal_id: str) -> TaskProposal | None:
        with closing(self._connect()) as database:
            row = database.execute(
                "SELECT * FROM task_capture_proposal WHERE proposal_id = ?", (proposal_id,)
            ).fetchone()
        return self._model(row)

    def save_proposal(self, proposal: TaskProposal) -> None:
        with closing(self._connect()) as database, database:
            database.execute(
                """INSERT INTO task_capture_proposal
                   (proposal_id, update_id, request_text, title, due_date, status, page_url)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(proposal_id) DO UPDATE SET
                    title=excluded.title, due_date=excluded.due_date, status=excluded.status,
                    page_url=excluded.page_url""",
                (
                    proposal.id,
                    proposal.update_id,
                    proposal.request_text,
                    proposal.draft.title,
                    proposal.draft.due_date.isoformat() if proposal.draft.due_date else None,
                    proposal.status,
                    proposal.page_url,
                ),
            )

    def claim_create(self, proposal_id: str) -> TaskProposal | None:
        with closing(self._connect()) as database, database:
            result = database.execute(
                "UPDATE task_capture_proposal SET status='creating' "
                "WHERE proposal_id=? AND status='pending'",
                (proposal_id,),
            )
            if result.rowcount != 1:
                return None
            row = database.execute(
                "SELECT * FROM task_capture_proposal WHERE proposal_id = ?", (proposal_id,)
            ).fetchone()
        return self._model(row)

    def finish(self, proposal_id: str, status: str, page_url: str = "") -> None:
        with closing(self._connect()) as database, database:
            database.execute(
                "UPDATE task_capture_proposal SET status=?, page_url=? WHERE proposal_id=?",
                (status, page_url, proposal_id),
            )

    def recover_inflight(self) -> None:
        with closing(self._connect()) as database, database:
            database.execute(
                "UPDATE task_capture_proposal SET status='uncertain' WHERE status='creating'"
            )

    def _connect(self) -> sqlite3.Connection:
        database = sqlite3.connect(self.path, timeout=10)
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA busy_timeout=10000")
        return database

    @staticmethod
    def _model(row: sqlite3.Row | None) -> TaskProposal | None:
        if row is None:
            return None
        due = date.fromisoformat(row["due_date"]) if row["due_date"] else None
        return TaskProposal(
            row["proposal_id"],
            row["update_id"],
            row["request_text"],
            TaskDraft(row["title"], due),
            row["status"],
            row["page_url"],
        )
