"""Provider-independent orchestration for factual narrative reports."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Protocol

from .models import Period, ReportItem
from .ports import ReportSource

EMPTY_REPORT = (
    "За этот период в Notion нет записей с датой выполнения или рабочей активности; "
    "это не означает, что ничего не сделано."
)

_PROJECT_ID = re.compile(r"\bREP-\d+\b", re.IGNORECASE)
_LEADING_TIMESTAMP = re.compile(
    r"^\s*(?:\[|\()?\s*(?:"
    r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[./-]\d{1,2}[./-]\d{2,4}"
    r")(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?\s*(?:\]|\))?\s*[-–—|:]?\s*"
)
_SNAPSHOT_TIMESTAMP = re.compile(
    r"\b(?:snapshot|снимок)\b\s*[:#-]?\s*"
    r"(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[./-]\d{1,2}[./-]\d{2,4})?"
    r"(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?",
    re.IGNORECASE,
)
_DATE_OR_TIME = re.compile(
    r"\b(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|"
    r"\d{1,2}:\d{2}(?::\d{2})?)\b"
)
_SPACES = re.compile(r"\s+")


class SummaryGenerator(Protocol):
    def summarize(self, period: Period, items: Sequence[ReportItem]) -> str: ...


@dataclass
class NarrativeReports:
    sources: Sequence[ReportSource]
    generator: SummaryGenerator

    def build(self, period: Period) -> str:
        unique_items: list[ReportItem] = []
        seen: set[tuple[str, str]] = set()
        for source in self.sources:
            for item in source.items(period):
                identity = (item.id, item.section)
                if identity not in seen:
                    seen.add(identity)
                    unique_items.append(item)

        if not unique_items:
            body = EMPTY_REPORT
        else:
            body = self.generator.summarize(period, _group_work_activity(unique_items))
        if len(body) > 3_300:
            raise RuntimeError("Narrative report body exceeds 3300 characters")
        report = f"{_period_header(period)}\n\n{body}"
        if len(report) > 3_500:
            raise RuntimeError("Narrative report exceeds 3500 characters")
        return report


def _group_work_activity(items: Sequence[ReportItem]) -> list[ReportItem]:
    grouped: dict[tuple[str, str], list[ReportItem]] = {}
    result: list[ReportItem] = []
    ordered = sorted(
        items,
        key=lambda item: (
            _recorded_datetime(item),
            item.section,
            item.title.casefold(),
            item.id,
        ),
    )
    for item in ordered:
        if item.section != "work":
            result.append(item)
            continue
        project_id = _project_id(item)
        clean_title = _clean_work_title(item.title)
        normalized_title = _normalize_title(clean_title)
        if project_id:
            key = ("work-project", project_id.casefold())
        elif normalized_title:
            key = ("work-title", normalized_title)
        else:
            key = ("work-item", item.id)
        grouped.setdefault(key, []).append(item)

    for key, group in grouped.items():
        project_id = key[1] if key[0] == "work-project" else ""
        titles = _unique_text(_clean_work_title(item.title) for item in group)
        title = project_id.upper() if project_id else (titles[0] if titles else "Рабочий проект")
        dated_facts: dict[str, tuple[datetime, str, str]] = {}
        for item in group:
            for fact in (_clean_work_title(item.title), item.details.strip()):
                normalized_fact = _normalize_title(fact)
                if normalized_fact:
                    recorded_at = item.recorded_at or item.completed_on.isoformat()
                    dated_facts[normalized_fact] = (
                        _recorded_datetime(item),
                        recorded_at,
                        fact,
                    )
        facts = [
            f"{recorded_at} — {fact}"
            for _, recorded_at, fact in sorted(dated_facts.values(), key=lambda value: value[0])
        ]
        latest = max(group, key=_recorded_datetime)
        result.append(
            ReportItem(
                id=f"work-group:{key[1]}",
                title=title,
                completed_on=latest.completed_on,
                url="",
                section="work",
                details="\n".join(facts),
                recorded_at=latest.recorded_at,
            )
        )
    return sorted(
        result,
        key=lambda item: (
            _recorded_datetime(item),
            item.section,
            item.title.casefold(),
            item.id,
        ),
    )


def _period_header(period: Period) -> str:
    label = {"week": "Неделя", "month": "Месяц"}.get(period.kind, period.kind)
    last_day = date.fromordinal(period.end.toordinal() - 1)
    return f"{label} {period.start:%d.%m.%Y}–{last_day:%d.%m.%Y}"


def _recorded_datetime(item: ReportItem) -> datetime:
    recorded_at = item.recorded_at
    try:
        if "T" in recorded_at:
            parsed = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
        elif recorded_at:
            parsed = datetime.combine(date.fromisoformat(recorded_at), time.min)
        else:
            parsed = datetime.combine(item.completed_on, time.min)
    except ValueError:
        parsed = datetime.combine(item.completed_on, time.min)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _project_id(item: ReportItem) -> str:
    match = _PROJECT_ID.search(item.title)
    return match.group(0) if match else ""


def _clean_work_title(title: str) -> str:
    cleaned = title.strip()
    while match := _LEADING_TIMESTAMP.match(cleaned):
        cleaned = cleaned[match.end() :]
    cleaned = _SNAPSHOT_TIMESTAMP.sub(" ", cleaned)
    cleaned = _DATE_OR_TIME.sub(" ", cleaned)
    return _SPACES.sub(" ", cleaned).strip(" -–—|:[]()")


def _normalize_title(title: str) -> str:
    return _SPACES.sub(" ", title.casefold()).strip(" -–—|:[]()")


def _unique_text(values: Iterable[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _SPACES.sub(" ", value).strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            unique.append(text)
    return unique
