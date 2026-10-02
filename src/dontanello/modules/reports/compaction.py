"""Deterministic compaction of repeated observations before report analysis."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable

from .progress_models import Evidence


@dataclass(frozen=True)
class NormalizedEvent:
    """A meaningful observation with traceable links to every original record."""

    id: str
    project: str
    source_id: str
    event_type: str
    occurred_on: date
    first_recorded_at: str
    last_recorded_at: str
    text: str
    source_kind: str
    source_snapshot_ids: tuple[str, ...]
    url: str

    def as_evidence(self) -> Evidence:
        return Evidence(
            id=self.id,
            source_id=self.source_id,
            project=self.project,
            occurred_on=self.occurred_on,
            recorded_at=self.last_recorded_at,
            text=self.text,
            source_kind="normalized_event",
            url=self.url,
            source_ids=self.source_snapshot_ids,
            event_type=self.event_type,
            first_recorded_at=self.first_recorded_at,
            observation_count=len(self.source_snapshot_ids),
        )


@dataclass(frozen=True)
class ProjectTimeline:
    project: str
    event_ids: tuple[str, ...]
    start_event_id: str
    end_event_id: str


def compact_observations(records: Iterable[Evidence]) -> tuple[Evidence, ...]:
    """Collapse only adjacent equivalent observations within each project.

    Punctuation and whitespace-only edits are ignored. Version numbers, negation,
    identifiers, and event order remain significant. Originals are not modified.
    """
    ordered = sorted(records, key=lambda item: (item.occurred_on, item.recorded_at, item.id))
    grouped: list[list[Evidence]] = []
    last_by_project: dict[tuple[str, str], int] = {}
    signatures: dict[int, str] = {}
    for record in ordered:
        project_key = (record.project.casefold().strip(), record.source_kind)
        signature = _signature(record.text)
        previous_index = last_by_project.get(project_key)
        if (
            previous_index is not None
            and signatures[previous_index] == signature
            and _within_snapshot_window(grouped[previous_index][-1], record)
        ):
            grouped[previous_index].append(record)
            continue
        last_by_project[project_key] = len(grouped)
        signatures[len(grouped)] = signature
        grouped.append([record])

    events = [_event(group).as_evidence() for group in grouped]
    return tuple(sorted(events, key=lambda item: (item.occurred_on, item.recorded_at, item.id)))


def project_timelines(events: Iterable[Evidence]) -> tuple[ProjectTimeline, ...]:
    grouped: dict[str, list[Evidence]] = defaultdict(list)
    for event in events:
        grouped[event.project].append(event)
    timelines: list[ProjectTimeline] = []
    for project, unsorted_items in sorted(grouped.items(), key=lambda pair: pair[0].casefold()):
        items = sorted(
            unsorted_items, key=lambda item: (item.occurred_on, item.recorded_at, item.id)
        )
        timelines.append(
            ProjectTimeline(
                project=project,
                event_ids=tuple(item.id for item in items),
                start_event_id=items[0].id,
                end_event_id=items[-1].id,
            )
        )
    return tuple(timelines)


def _event(records: list[Evidence]) -> NormalizedEvent:
    first = records[0]
    last = records[-1]
    source_ids = tuple(
        dict.fromkeys(
            source_id for item in records for source_id in (item.source_ids or (item.id,))
        )
    )
    identity = "\0".join((first.project.casefold(), first.source_kind, *source_ids))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    event_type = (
        first.event_type
        if first.source_kind == "normalized_event"
        else ("completed_task" if first.source_kind in {"tasks", "goals"} else "work_observation")
    )
    return NormalizedEvent(
        id=f"evt:{digest}",
        project=first.project,
        source_id=first.source_id,
        event_type=event_type,
        occurred_on=last.occurred_on,
        first_recorded_at=first.first_recorded_at or first.recorded_at,
        last_recorded_at=last.recorded_at,
        text=first.text,
        source_kind=first.source_kind,
        source_snapshot_ids=source_ids,
        url=last.url or first.url,
    )


def _signature(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(
        r"\s*[—–]\s*\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?(?:\s+\d{1,2}:\d{2})?\s*$",
        "",
        normalized,
    )
    normalized = re.sub(r"\s+", " ", normalized).strip()
    # Ignore sentence punctuation while preserving punctuation inside identifiers
    # (for example v1.2), URLs, and negation.
    normalized = re.sub(r"(?<=[\w)])[,;:!?]+(?=\s|$)", "", normalized)
    normalized = re.sub(r"(?<=[\w)])\.(?=\s|$)", "", normalized)
    return normalized


def _within_snapshot_window(previous: Evidence, current: Evidence) -> bool:
    """Only merge frequent polling snapshots, never repeated work on another day."""
    if "T" not in previous.recorded_at or "T" not in current.recorded_at:
        return previous.recorded_at == current.recorded_at
    try:
        previous_time = datetime.fromisoformat(previous.recorded_at.replace("Z", "+00:00"))
        current_time = datetime.fromisoformat(current.recorded_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if (previous_time.tzinfo is None) != (current_time.tzinfo is None):
        return False
    return timedelta(0) <= current_time - previous_time <= timedelta(minutes=15)
