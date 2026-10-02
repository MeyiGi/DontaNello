"""Application pipeline for cached, evidence-grounded progress reports."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timedelta, timezone

from .application import previous_month, previous_week
from .evidence import collect_evidence, validate_analysis
from .models import Period, ReportItem
from .ports import ReportSource
from .progress_models import (
    Analysis,
    Evidence,
    HistoricalContext,
    ProgressDocument,
    ReportSection,
)
from .progress_ports import ProgressAnalyzer, ProgressArchive, ReportStrategy
from .strategies import MonthlyStrategy, WeeklyStrategy

StrategyFactory = Callable[[], ReportStrategy]
StrategyRegistry = dict[str, StrategyFactory]


class ProgressReports:
    """Collect full evidence, request a grounded reflection, validate and archive it."""

    def __init__(
        self,
        sources: Sequence[ReportSource],
        analyzer: ProgressAnalyzer,
        archive: ProgressArchive,
        scope: str,
        clock: Callable[[], datetime],
        strategies: StrategyRegistry | None = None,
    ) -> None:
        self.sources = tuple(sources)
        self.analyzer = analyzer
        self.archive = archive
        self.scope = scope
        self.clock = clock
        self.strategies: StrategyRegistry = (
            strategies
            if strategies is not None
            else {
                "week": WeeklyStrategy,
                "month": MonthlyStrategy,
            }
        )

    def build(self, period: Period) -> ProgressDocument:
        """Return a structured report, using an archived result before any reads."""
        cached = self.archive.get(self.scope, period)
        if cached is not None:
            return cached

        strategy = self._strategy(period)
        current_evidence = self._collect(period)
        baseline_evidence = self._collect(_comparison_period(period)) if current_evidence else ()
        historical = self.archive.context(self.scope, period)
        prior_evidence = _historical_evidence(historical, period)
        analysis = Analysis()
        if current_evidence:
            comparison_evidence = _unique_evidence((*baseline_evidence, *prior_evidence))
            context = HistoricalContext(evidence=comparison_evidence, reports=historical.reports)
            analysis = self.analyzer.analyze(
                period,
                current_evidence,
                context,
                strategy.specification(),
            )
            known_evidence = _unique_evidence((*current_evidence, *comparison_evidence))
            analysis = validate_analysis(analysis, strategy.specification(), known_evidence, period)

        sections = strategy.sections(period, analysis)
        if analysis.notices:
            message = "При проверке сняты выводы, которые не удалось подтвердить точными цитатами из записей."
            if analysis.notices:
                message = "\n".join((message, *analysis.notices))
            sections = _add_notice(sections, message)
        document_evidence = _document_evidence(
            current_evidence,
            sections,
            _unique_evidence((*baseline_evidence, *prior_evidence)),
        )
        document = ProgressDocument(
            period=period,
            title=strategy.title(period),
            sections=sections,
            evidence=document_evidence,
            generated_at=_aware(self.clock()),
            analysis_metrics=analysis.metrics,
        )
        self.render(document)
        return self.archive.save(self.scope, document)

    def render(self, document: ProgressDocument) -> str:
        """Render a structured document without shortening its evidence or findings."""
        from .rendering import render_progress

        return render_progress(document, self._strategy(document.period).specification())

    def report(self, period: Period) -> str:
        """Build and render the report for delivery adapters."""
        return self.render(self.build(period))

    def _collect(self, period: Period) -> tuple[Evidence, ...]:
        items: list[ReportItem] = []
        for source in self.sources:
            items.extend(
                item
                for item in source.items(period)
                if period.start <= item.completed_on < period.end
            )
        return collect_evidence(items)

    def _strategy(self, period: Period) -> ReportStrategy:
        try:
            factory = self.strategies[period.kind]
        except KeyError as exc:
            raise ValueError(f"No progress strategy configured for {period.kind!r}") from exc
        return factory()


def _comparison_period(period: Period) -> Period:
    if period.kind == "week":
        if period.start.weekday() == 0 and period.end - period.start == timedelta(days=7):
            return previous_week(period.start)
        length = period.end - period.start
        return Period("week", period.start - length, period.start)
    if period.kind == "month":
        if period.start.day == 1:
            return previous_month(period.start)
        length = period.end - period.start
        return Period("month", period.start - length, period.start)
    return Period(period.kind, period.start - (period.end - period.start), period.start)


def _historical_evidence(context: HistoricalContext, period: Period) -> tuple[Evidence, ...]:
    """Flatten archived reports to original evidence and discard future records."""
    candidates = list(context.evidence)
    for report in context.reports:
        candidates.extend(report.evidence)
    return _unique_evidence(entry for entry in candidates if entry.occurred_on < period.end)


def _unique_evidence(entries: Iterable[Evidence]) -> tuple[Evidence, ...]:
    unique: dict[str, Evidence] = {}
    for entry in entries:
        unique.setdefault(entry.id, entry)
    return tuple(
        sorted(unique.values(), key=lambda value: (value.occurred_on, value.recorded_at, value.id))
    )


def _document_evidence(
    period_evidence: Iterable[Evidence],
    sections: Sequence[ReportSection],
    historical: Sequence[Evidence],
) -> tuple[Evidence, ...]:
    required = {
        citation.evidence_id
        for section in sections
        for finding in section.findings
        for citation in finding.citations
    }
    entries = {entry.id: entry for entry in period_evidence}
    entries.update({entry.id: entry for entry in historical if entry.id in required})
    return tuple(
        sorted(entries.values(), key=lambda value: (value.occurred_on, value.recorded_at, value.id))
    )


def _add_notice(sections: tuple[ReportSection, ...], notice: str) -> tuple[ReportSection, ...]:
    if not sections:
        return sections
    first = sections[0]
    note = f"{first.note}\n{notice}".strip()
    return (ReportSection(first.key, first.title, first.findings, note), *sections[1:])


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
