"""Private durable storage for personal calendar planning proposals."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

from ..models import PlanProposal, TimeSlot


class SQLitePlanningRepository:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with closing(self._connect()) as database, database:
            database.execute("PRAGMA journal_mode=WAL")
            database.execute(
                """CREATE TABLE IF NOT EXISTS calendar_planning_proposal (
                    proposal_id TEXT PRIMARY KEY,
                    update_id INTEGER NOT NULL UNIQUE,
                    request_text TEXT NOT NULL,
                    title TEXT NOT NULL,
                    day TEXT NOT NULL,
                    duration_minutes INTEGER NOT NULL,
                    options TEXT NOT NULL,
                    selected_index INTEGER,
                    status TEXT NOT NULL CHECK(status IN
                        ('pending','creating','created','cancelled','deleting','deleted')),
                    event_id TEXT NOT NULL DEFAULT ''
                )"""
            )
        os.chmod(path, 0o600)

    def proposal_for_update(self, update_id: int) -> PlanProposal | None:
        with closing(self._connect()) as database:
            row = database.execute(
                "SELECT * FROM calendar_planning_proposal WHERE update_id = ?", (update_id,)
            ).fetchone()
        return self._model(row)

    def get_proposal(self, proposal_id: str) -> PlanProposal | None:
        with closing(self._connect()) as database:
            row = database.execute(
                "SELECT * FROM calendar_planning_proposal WHERE proposal_id = ?", (proposal_id,)
            ).fetchone()
        return self._model(row)

    def save_proposal(self, proposal: PlanProposal) -> None:
        options = json.dumps(
            [
                {"start": slot.start.isoformat(), "end": slot.end.isoformat()}
                for slot in proposal.options
            ],
            separators=(",", ":"),
        )
        with closing(self._connect()) as database, database:
            database.execute(
                """INSERT INTO calendar_planning_proposal
                   (proposal_id, update_id, request_text, title, day, duration_minutes,
                    options, selected_index, status, event_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(proposal_id) DO UPDATE SET
                    request_text=excluded.request_text, title=excluded.title, day=excluded.day,
                    duration_minutes=excluded.duration_minutes, options=excluded.options,
                    selected_index=excluded.selected_index, status=excluded.status,
                    event_id=excluded.event_id""",
                (
                    proposal.id,
                    proposal.update_id,
                    proposal.request_text,
                    proposal.title,
                    proposal.day.isoformat(),
                    proposal.duration_minutes,
                    options,
                    proposal.selected_index,
                    proposal.status,
                    proposal.event_id,
                ),
            )

    def _connect(self) -> sqlite3.Connection:
        database = sqlite3.connect(self.path, timeout=10)
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA busy_timeout=10000")
        return database

    @staticmethod
    def _model(row: sqlite3.Row | None) -> PlanProposal | None:
        if row is None:
            return None
        raw_options = json.loads(row["options"])
        options = tuple(
            TimeSlot(datetime.fromisoformat(item["start"]), datetime.fromisoformat(item["end"]))
            for item in raw_options
        )
        return PlanProposal(
            id=row["proposal_id"],
            update_id=row["update_id"],
            request_text=row["request_text"],
            title=row["title"],
            day=date.fromisoformat(row["day"]),
            duration_minutes=row["duration_minutes"],
            options=options,
            selected_index=row["selected_index"],
            status=row["status"],
            event_id=row["event_id"],
        )
