"""Deterministic rendering for structured progress documents."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime

from .progress_models import Evidence, Finding, ProgressDocument, StrategySpec

_STATUS_LABELS = {
    "external_blocked": "Внешняя зависимость",
    "postponed": "Отложено",
    "in_progress": "В работе",
    "abandoned": "Отменено",
    "unknown": "Статус неизвестен",
}


def render_progress(document: ProgressDocument, strategy: StrategySpec) -> str:
    """Render every section and full finding without row dumps or truncation."""
    lines = [document.title]
    evidence = {entry.id: entry for entry in document.evidence}
    for section in document.sections:
        lines.extend(("", section.title))
        if section.findings:
            for label, findings in _group_findings(section.key, section.findings):
                if label:
                    lines.append(f"{label}:")
                for finding in findings:
                    text = _finding_text(finding, evidence)
                    if section.key in {"transformation", "reflection"}:
                        lines.append(text)
                    else:
                        prefix = (
                            f"{finding.project} — "
                            if finding.project and section.key != "projects"
                            else ""
                        )
                        lines.append(f"• {prefix}{text}")
        if section.note:
            lines.append(section.note)
        elif not section.findings:
            lines.append("В источниках нет подтверждённых данных для этого раздела.")
    report = "\n".join(lines)
    word_count = len(report.split())
    if word_count > strategy.max_words:
        raise ValueError(
            f"Progress report has {word_count} words; strategy word limit is {strategy.max_words}"
        )
    if len(report) > strategy.max_chars:
        raise ValueError(
            f"Progress report has {len(report)} characters; strategy limit is {strategy.max_chars}"
        )
    return report


def _finding_text(finding: Finding, evidence: dict[str, Evidence]) -> str:
    parts = [finding.text]
    movement = [part for part in (finding.before, finding.action, finding.after) if part]
    if movement:
        parts.append(" → ".join(movement))
    if finding.kind in {"comparison", "trajectory"}:
        before = [
            evidence[identifier] for identifier in finding.before_ids if identifier in evidence
        ]
        after = [evidence[identifier] for identifier in finding.after_ids if identifier in evidence]
        if before and after:
            same_day = {entry.occurred_on for entry in (*before, *after)}
            parts.insert(
                0, f"{_dates(before, len(same_day) == 1)} → {_dates(after, len(same_day) == 1)}:"
            )
    if finding.kind == "unfinished" and finding.status:
        parts.insert(0, f"{_STATUS_LABELS.get(finding.status, finding.status)}:")
    return " ".join(parts)


def _dates(evidence: Sequence[Evidence], include_time: bool) -> str:
    labels: dict[str, None] = {}
    for entry in sorted(evidence, key=lambda item: (item.occurred_on, item.recorded_at)):
        label = entry.occurred_on.strftime("%d.%m.%Y")
        if include_time and ("T" in entry.recorded_at or " " in entry.recorded_at):
            try:
                recorded_at = datetime.fromisoformat(entry.recorded_at.replace("Z", "+00:00"))
            except ValueError:
                pass
            else:
                label = recorded_at.strftime("%d.%m.%Y %H:%M %z").strip()
        labels[label] = None
    return ", ".join(labels)


def _group_findings(
    section_key: str, findings: tuple[Finding, ...]
) -> list[tuple[str, Sequence[Finding]]]:
    if section_key not in {"learning", "projects"}:
        return [("", findings)]
    grouped: dict[str, list[Finding]] = defaultdict(list)
    labels: dict[str, str] = {}
    for finding in findings:
        value = (finding.area if section_key == "learning" else finding.project).strip()
        label = value or ("Область не указана" if section_key == "learning" else "Проект не указан")
        key = label.casefold()
        labels.setdefault(key, label)
        grouped[key].append(finding)
    return [(labels[key], grouped[key]) for key in sorted(grouped)]
