"""Distinct weekly and monthly progress-report strategies."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, timedelta

from .models import Period
from .progress_models import (
    Analysis,
    Finding,
    ReportSection,
    SectionSpec,
    StrategySpec,
)

_WEEKLY_SECTIONS = (
    SectionSpec(
        "transformation",
        "🏆 Главное изменение недели",
        ("transformation",),
        1,
        "One shared 2–4 sentence trajectory; synthesize the common change, do not list projects or repeat Progress.",
    ),
    SectionSpec(
        "progress",
        "✅ Что реально продвинулось",
        ("progress", "achievement"),
        7,
        "Choose 4–7 meaningful results when supported. Group by project; activity and intention are not results.",
    ),
    SectionSpec(
        "learning",
        "🧠 Чему я научился / что теперь понимаю лучше",
        ("learning",),
        4,
        "Give 2–4 modest learnings; infer from investigation progression, changed hypotheses, tools or action. Match the claim to the evidence; no mastery claims.",
    ),
    SectionSpec(
        "comparison",
        "📈 Я неделю назад → я сейчас",
        ("comparison", "trajectory"),
        3,
        "Give 1–3 dated before → now comparisons in the same project; do not repeat Progress details.",
    ),
    SectionSpec(
        "blockers",
        "⏸️ Что сейчас зависит не от меня",
        ("blocker",),
        5,
        "Group by project and latest state: what the user did → current external dependency. Drop superseded blockers.",
    ),
    SectionSpec(
        "next",
        "🎯 Что продолжить на следующей неделе",
        ("next_step",),
        5,
        "Up to 3 independent steps first, ranked by active work and specificity; then at most 2 conditional steps. No backlog or new goals.",
    ),
    SectionSpec(
        "ideas",
        "💡 Идеи отдельно от результатов",
        ("idea",),
        3,
        "Keep ideas separate from achievements and chosen actions.",
    ),
)

_MONTHLY_SECTIONS = (
    SectionSpec(
        "transformation",
        "🚀 Главная трансформация месяца",
        ("transformation",),
        1,
        "Explain the month's strongest trajectory from beginning state to end state. Do not lead with blockers or activity counts.",
    ),
    SectionSpec(
        "achievements",
        "🏆 Главные достижения месяца",
        ("achievement",),
        10,
        "Give 5–10 completed outcomes when supported by current-period completion evidence; do not pad the list.",
    ),
    SectionSpec(
        "learning",
        "🧠 Что я теперь умею лучше",
        ("learning",),
        8,
        "Group repeated learning by skill area. Infer modest practice or understanding from real technical work or course exposure, without inventing mastery.",
    ),
    SectionSpec(
        "projects",
        "📊 Проекты, которые реально сдвинулись",
        ("progress",),
        7,
        "Group repeated project records and distinguish activity from completion.",
    ),
    SectionSpec(
        "comparison",
        "📈 Месяц назад → сейчас",
        ("comparison", "trajectory"),
        7,
        "Give 3–7 comparisons when supported; identify actual baseline dates and current-period after states in the same project.",
    ),
    SectionSpec(
        "patterns",
        "🔁 Что повторялось",
        ("pattern",),
        4,
        "Describe repeated observed evidence, without assumed causes, psychological judgments, praise or invented KPIs.",
    ),
    SectionSpec(
        "unfinished",
        "⏸️ Что осталось незакрытым — и почему",
        ("unfinished", "blocker"),
        6,
        "Use the supported logical status external_blocked, postponed, in_progress, abandoned or unknown; label unknown reasons as unknown and do not infer a cause.",
    ),
    SectionSpec(
        "next",
        "🎯 Фокус следующего месяца",
        ("next_step",),
        5,
        "Derive a next concrete action from the latest state of an existing project when reasonable. Do not create a new project or change the user's goal.",
    ),
    SectionSpec(
        "reflection",
        "💬 Одна итоговая мысль месяца",
        ("reflection",),
        1,
        "Summarize only conclusions grounded in the evidence.",
    ),
)


class WeeklyStrategy:
    """Structure a one-week reflection around a short-term comparison."""

    def specification(self) -> StrategySpec:
        return StrategySpec(
            kind="week",
            instructions=(
                "Write a reflective weekly progress report in Russian. Keep the existing section order: "
                "progress, learning, dated comparison, external blockers, next actions, ideas. The main "
                "change is one 2–4 sentence shared trajectory, not a list of projects. Keep each fact "
                "in the section that answers its question and avoid repeating the same result across "
                "sections. Raw activity is not completion. Separate ideas from results. Cite every "
                "finding. Give 4–7 meaningful results and 2–4 learning points when supported, "
                "1–3 concise dated comparisons, three prioritized independent next actions plus at most "
                "two conditional actions, and compact current blockers grouped by project. Do not pad "
                "empty sections. Historical evidence is context, never new progress. No psychological "
                "judgments, praise, invented KPIs or guessed causes. Use natural Russian; state gaps "
                "only when needed to explain uncertainty."
            ),
            sections=_WEEKLY_SECTIONS,
            max_words=700,
            max_chars=12_000,
        )

    def title(self, period: Period) -> str:
        last_day = period.end - timedelta(days=1)
        return f"Неделя {period.start:%d.%m.%Y}–{last_day:%d.%m.%Y}"

    def sections(self, period: Period, analysis: Analysis) -> tuple[ReportSection, ...]:
        return _make_sections(_WEEKLY_SECTIONS, analysis, period)


class MonthlyStrategy:
    """Structure a month-long reflection around trajectory and recurring themes."""

    def specification(self) -> StrategySpec:
        return StrategySpec(
            kind="month",
            instructions=(
                "Write a reflective monthly progress report in Russian. Explain the transformation, "
                "achievement, skill growth and movement by project, then dated comparison, repeated "
                "patterns, unfinished work, next-month focus and one grounded reflection. Group "
                "repeated skill evidence by area and project evidence by project. Raw activity is not "
                "completion. Cite every finding. Give 5–10 meaningful achievements when supported, "
                "plus the distinct trajectory, learning, repeated patterns, unfinished work and up "
                "to 5 next actions and 3–7 dated comparisons when supported. "
                "Historical evidence is context, never new progress. No psychological judgments, "
                "praise, invented KPIs or guessed causes. Do not pad empty sections."
            ),
            sections=_MONTHLY_SECTIONS,
            max_words=1_500,
            max_chars=27_000,
        )

    def title(self, period: Period) -> str:
        month = _month_name(period.start)
        return f"📆 {month} {period.start.year} — Monthly Progress Review"

    def sections(self, period: Period, analysis: Analysis) -> tuple[ReportSection, ...]:
        return _make_sections(_MONTHLY_SECTIONS, analysis, period)


def strategy_for(period: Period) -> WeeklyStrategy | MonthlyStrategy:
    """Choose a strategy by period kind; add new strategies as separate classes."""
    strategies: dict[str, Callable[[], WeeklyStrategy | MonthlyStrategy]] = {
        "week": WeeklyStrategy,
        "month": MonthlyStrategy,
    }
    try:
        return strategies[period.kind]()
    except KeyError as exc:
        raise ValueError(f"Unsupported progress period kind: {period.kind}") from exc


def _make_sections(
    specs: tuple[SectionSpec, ...],
    analysis: Analysis,
    period: Period,
) -> tuple[ReportSection, ...]:
    sections: list[ReportSection] = []
    for spec in specs:
        candidates = [finding for finding in analysis.findings if finding.kind in spec.kinds]
        candidates = _deduplicate(candidates)
        selected = tuple(candidates[: spec.limit])
        note = ""
        if not selected:
            note = _empty_note(spec.key, period)
        sections.append(ReportSection(spec.key, spec.title, selected, note))
    return tuple(sections)


def _empty_note(key: str, period: Period) -> str:
    notes = {
        "learning": "В записях за этот период нет подтверждённых сведений об обучении.",
        "comparison": "Нет сопоставимых датированных данных; вывод о динамике не делается.",
        "blockers": "В записях нет подтверждённой внешней зависимости, которая задерживает работу.",
        "ideas": "Отдельных подтверждённых идей в записях периода нет.",
        "next": "В записях нет уже выбранного действия на следующий период.",
        "unfinished": "В записях нет подтверждённой незакрытой работы или причины задержки.",
        "patterns": "Записей недостаточно, чтобы подтвердить повторяющуюся закономерность.",
        "reflection": "Нет подтверждённого вывода для итоговой мысли месяца.",
    }
    if key in notes:
        return notes[key]
    return "В записях периода нет подтверждённых результатов для этого раздела."


def _deduplicate(findings: list[Finding]) -> list[Finding]:
    result: list[Finding] = []
    seen: set[tuple[str, str, str]] = set()
    for finding in findings:
        key = (
            finding.kind,
            finding.project.casefold().strip(),
            re.sub(r"\s+", " ", finding.text).casefold().strip(),
        )
        if key not in seen:
            seen.add(key)
            result.append(finding)
    return result


def _month_name(value: date) -> str:
    names = (
        "январь",
        "февраль",
        "март",
        "апрель",
        "май",
        "июнь",
        "июль",
        "август",
        "сентябрь",
        "октябрь",
        "ноябрь",
        "декабрь",
    )
    name = names[value.month - 1]
    return name.capitalize()
