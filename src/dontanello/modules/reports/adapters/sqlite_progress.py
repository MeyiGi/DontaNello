"""Private SQLite archive for structured progress reports and evidence."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path

from ..models import Period
from ..progress_models import (
    AnalysisMetrics,
    Citation,
    Evidence,
    Finding,
    HistoricalContext,
    ProgressDocument,
    ReportSection,
)

_SCHEMA_VERSION = 1
_HISTORY_DAYS = 366


class SQLiteProgressArchive:
    """Persist a single immutable progress document per scoped period."""

    def __init__(self, path: Path):
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, _SCHEMA_VERSION):
            connection.close()
            raise RuntimeError("Unsupported progress archive schema version")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS progress_document (
                scope TEXT NOT NULL,
                period_kind TEXT NOT NULL,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (scope, period_kind, period_start, period_end)
            );
            CREATE INDEX IF NOT EXISTS progress_period_lookup
                ON progress_document(scope, period_kind, period_start, period_end);
            """
        )
        connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        os.chmod(self.path, 0o600)
        return connection

    def get(self, scope: str, period: Period) -> ProgressDocument | None:
        connection = self._connect()
        try:
            row = connection.execute(
                """SELECT payload FROM progress_document
                   WHERE scope = ? AND period_kind = ? AND period_start = ? AND period_end = ?""",
                (scope, period.kind, period.start.isoformat(), period.end.isoformat()),
            ).fetchone()
            return _decode(row["payload"]) if row is not None else None
        finally:
            connection.close()

    def save(self, scope: str, document: ProgressDocument) -> ProgressDocument:
        if document.schema_version != 1:
            raise RuntimeError("Unsupported progress document schema version")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT OR IGNORE INTO progress_document
                   (scope, period_kind, period_start, period_end, payload)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    scope,
                    document.period.kind,
                    document.period.start.isoformat(),
                    document.period.end.isoformat(),
                    _encode(document),
                ),
            )
            row = connection.execute(
                """SELECT payload FROM progress_document
                   WHERE scope = ? AND period_kind = ? AND period_start = ? AND period_end = ?""",
                (
                    scope,
                    document.period.kind,
                    document.period.start.isoformat(),
                    document.period.end.isoformat(),
                ),
            ).fetchone()
            connection.commit()
            if row is None:
                raise RuntimeError("Progress document was not persisted")
            return _decode(row["payload"])
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def context(self, scope: str, period: Period) -> HistoricalContext:
        connection = self._connect()
        try:
            rows = connection.execute(
                """SELECT payload FROM progress_document
                   WHERE scope = ? AND period_end >= ? AND period_start < ? AND period_end <= ?
                   ORDER BY period_start, period_end, period_kind""",
                (
                    scope,
                    (period.start - timedelta(days=_HISTORY_DAYS)).isoformat(),
                    period.end.isoformat(),
                    period.end.isoformat(),
                ),
            ).fetchall()
            documents = tuple(_decode(row["payload"]) for row in rows)
        finally:
            connection.close()

        cutoff = period.start - timedelta(days=_HISTORY_DAYS)
        selected: list[ProgressDocument] = []
        for document in documents:
            archived = document.period
            if archived.end > period.end:
                continue
            if archived.kind == period.kind:
                if archived.end > period.start:
                    continue
                if archived.end <= cutoff:
                    continue
                selected.append(document)
            elif period.kind == "month" and archived.kind == "week":
                # Completed weekly reports inside the requested month give a
                # monthly summary current-period context without exposing later weeks.
                if (
                    archived.start < period.end
                    and archived.end > period.start
                    and archived.end <= period.end
                ):
                    selected.append(document)

        selected_keys = {_document_key(item) for item in selected}
        facts: dict[str, Evidence] = {}
        for document in documents:
            if document.period.end > period.start:
                continue
            for item in document.evidence:
                if not cutoff <= item.occurred_on < period.start:
                    continue
                if _document_key(document) in selected_keys:
                    continue
                facts.setdefault(item.id, item)

        selected.sort(key=lambda item: (item.period.start, item.period.end, item.period.kind))
        ordered_facts = tuple(sorted(facts.values(), key=lambda item: (item.occurred_on, item.id)))
        return HistoricalContext(evidence=ordered_facts, reports=tuple(selected))


def _document_key(document: ProgressDocument) -> tuple[str, date, date]:
    return (document.period.kind, document.period.start, document.period.end)


def _encode(document: ProgressDocument) -> str:
    return json.dumps(
        {
            "schema_version": document.schema_version,
            "analysis_metrics": asdict(document.analysis_metrics)
            if document.analysis_metrics
            else None,
            "strategy_version": document.strategy_version,
            "period": {
                "kind": document.period.kind,
                "start": document.period.start.isoformat(),
                "end": document.period.end.isoformat(),
            },
            "title": document.title,
            "generated_at": document.generated_at.isoformat(),
            "sections": [
                {
                    "key": section.key,
                    "title": section.title,
                    "note": section.note,
                    "findings": [_finding_dict(finding) for finding in section.findings],
                }
                for section in document.sections
            ],
            "evidence": [
                {
                    "id": item.id,
                    "source_id": item.source_id,
                    "project": item.project,
                    "occurred_on": item.occurred_on.isoformat(),
                    "recorded_at": item.recorded_at,
                    "text": item.text,
                    "source_kind": item.source_kind,
                    "url": item.url,
                    "source_ids": list(item.source_ids),
                    "event_type": item.event_type,
                    "first_recorded_at": item.first_recorded_at,
                    "observation_count": item.observation_count,
                }
                for item in document.evidence
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _finding_dict(finding: Finding) -> dict[str, object]:
    return {
        "kind": finding.kind,
        "project": finding.project,
        "text": finding.text,
        "citations": [
            {"evidence_id": citation.evidence_id, "quote": citation.quote}
            for citation in finding.citations
        ],
        "before": finding.before,
        "action": finding.action,
        "after": finding.after,
        "before_ids": list(finding.before_ids),
        "after_ids": list(finding.after_ids),
        "area": finding.area,
        "status": finding.status,
        "confidence": finding.confidence,
    }


def _decode(payload: str) -> ProgressDocument:
    value = json.loads(payload)
    if value.get("schema_version") != 1:
        raise RuntimeError("Unsupported progress document schema version")
    period = value["period"]
    return ProgressDocument(
        period=Period(
            kind=period["kind"],
            start=date.fromisoformat(period["start"]),
            end=date.fromisoformat(period["end"]),
        ),
        title=value["title"],
        sections=tuple(
            ReportSection(
                key=section["key"],
                title=section["title"],
                note=section["note"],
                findings=tuple(
                    Finding(
                        kind=finding["kind"],
                        project=finding["project"],
                        text=finding["text"],
                        citations=tuple(
                            Citation(citation["evidence_id"], citation["quote"])
                            for citation in finding["citations"]
                        ),
                        before=finding["before"],
                        action=finding["action"],
                        after=finding["after"],
                        before_ids=tuple(finding["before_ids"]),
                        after_ids=tuple(finding["after_ids"]),
                        area=finding["area"],
                        status=finding.get("status", ""),
                        confidence=finding.get("confidence", "medium"),
                    )
                    for finding in section["findings"]
                ),
            )
            for section in value["sections"]
        ),
        evidence=tuple(
            Evidence(
                id=item["id"],
                source_id=item["source_id"],
                project=item["project"],
                occurred_on=date.fromisoformat(item["occurred_on"]),
                recorded_at=item["recorded_at"],
                text=item["text"],
                source_kind=item["source_kind"],
                url=item["url"],
                source_ids=tuple(item.get("source_ids", ())),
                event_type=item.get("event_type", "observation"),
                first_recorded_at=item.get("first_recorded_at", ""),
                observation_count=item.get("observation_count", 1),
            )
            for item in value["evidence"]
        ),
        generated_at=datetime.fromisoformat(value["generated_at"]),
        strategy_version=value["strategy_version"],
        schema_version=value["schema_version"],
        analysis_metrics=AnalysisMetrics(**value["analysis_metrics"])
        if value.get("analysis_metrics")
        else None,
    )
