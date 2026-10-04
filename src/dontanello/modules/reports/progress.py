"""Application pipeline for cached, evidence-grounded progress reports."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from .application import previous_month, previous_week
from .compaction import compact_observations
from .evidence import collect_evidence, validate_analysis
from .models import Period, ReportItem
from .ports import ReportSource
from .progress_models import (
    Analysis,
    AnalysisMetrics,
    Evidence,
    HistoricalContext,
    ProgressDocument,
    ReportSection,
)
from .progress_ports import ProgressAnalyzer, ProgressArchive, ReportStrategy
from .strategies import MonthlyStrategy, WeeklyStrategy

StrategyFactory = Callable[[], ReportStrategy]
StrategyRegistry = dict[str, StrategyFactory]
_LOGGER = logging.getLogger(__name__)


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
        historical = self.archive.context(self.scope, period)
        current_raw = self._collect(period)
        current_evidence = _period_events(current_raw, historical, period)
        baseline_raw: tuple[Evidence, ...]
        baseline_evidence: tuple[Evidence, ...]
        baseline_report = (
            _previous_month_report(historical, period) if period.kind == "month" else None
        )
        if baseline_report is not None:
            baseline_raw = ()
            baseline_evidence = tuple(
                entry
                for entry in baseline_report.evidence
                if entry.source_kind == "normalized_event"
            )
        else:
            baseline_raw = self._collect(_comparison_period(period)) if current_raw else ()
            baseline_evidence = compact_observations(baseline_raw)
        prior_raw = _historical_evidence(historical, period)
        prior_evidence = compact_observations(prior_raw)
        analysis = Analysis()
        if current_evidence:
            comparison_evidence = _unique_evidence((*baseline_evidence, *prior_evidence))
            context = HistoricalContext(evidence=comparison_evidence, reports=historical.reports)
            _LOGGER.info(
                "Progress source coverage: records_total=%d records_processed=%d events_created=%d projects_covered=%d",
                len(current_raw),
                len(current_raw),
                len(current_evidence),
                len({entry.project for entry in current_evidence}),
            )
            analysis = self.analyzer.analyze(
                period,
                current_evidence,
                context,
                strategy.specification(),
            )
            known_evidence = _unique_evidence(
                (
                    *current_raw,
                    *current_evidence,
                    *baseline_raw,
                    *baseline_evidence,
                    *prior_raw,
                    *prior_evidence,
                )
            )
            analysis = validate_analysis(analysis, strategy.specification(), known_evidence, period)

        metrics = analysis.metrics or AnalysisMetrics()
        analysis = replace(
            analysis,
            metrics=replace(
                metrics,
                records_total=len(current_raw),
                records_processed=len(current_raw),
                events_created=len(current_evidence),
                projects_covered=len({entry.project for entry in current_evidence}),
            ),
        )

        sections = strategy.sections(period, analysis)
        if analysis.notices:
            _LOGGER.info(
                "Progress analysis completed with %d internal notices", len(analysis.notices)
            )
        document_evidence = _document_evidence(
            (*current_raw, *current_evidence),
            sections,
            _unique_evidence((*baseline_raw, *baseline_evidence, *prior_raw, *prior_evidence)),
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
        if period.end - period.start == timedelta(days=7):
            return previous_week(period.start, period.start.weekday())
        length = period.end - period.start
        return Period("week", period.start - length, period.start)
    if period.kind == "month":
        if period.start.day == 1:
            return previous_month(period.start)
        length = period.end - period.start
        return Period("month", period.start - length, period.start)
    return Period(period.kind, period.start - (period.end - period.start), period.start)


def _historical_evidence(context: HistoricalContext, period: Period) -> tuple[Evidence, ...]:
    """Prefer archived events; use original records only where no event covers them."""
    candidates = list(context.evidence)
    for report in context.reports:
        report_events = [
            entry for entry in report.evidence if entry.source_kind == "normalized_event"
        ]
        candidates.extend(report_events or report.evidence)
    events = [
        entry
        for entry in candidates
        if entry.source_kind == "normalized_event" and entry.occurred_on < period.end
    ]
    covered_ids = {source_id for event in events for source_id in event.source_ids}
    return _unique_evidence(
        entry
        for entry in candidates
        if entry.occurred_on < period.end
        and (
            entry.source_kind == "normalized_event"
            or (entry.id not in covered_ids and entry.source_kind != "normalized_event")
        )
    )


def _period_events(
    raw: Sequence[Evidence], context: HistoricalContext, period: Period
) -> tuple[Evidence, ...]:
    """Reuse completed weekly event snapshots for a monthly review where available."""
    if period.kind != "month":
        return compact_observations(raw)
    weekly: list[Evidence] = []
    for report in context.reports:
        if (
            report.period.kind == "week"
            and report.period.start < period.end
            and report.period.end > period.start
            and report.period.end <= period.end
        ):
            weekly.extend(
                entry
                for entry in report.evidence
                if entry.source_kind == "normalized_event"
                and period.start <= entry.occurred_on < period.end
            )
    covered_ids = {source_id for event in weekly for source_id in event.source_ids}
    uncovered = tuple(entry for entry in raw if entry.id not in covered_ids)
    return _unique_evidence((*weekly, *compact_observations(uncovered)))


def _previous_month_report(context: HistoricalContext, period: Period) -> ProgressDocument | None:
    candidates = [
        report
        for report in context.reports
        if report.period.kind == "month" and report.period.end <= period.start
    ]
    return max(candidates, key=lambda report: report.period.end, default=None)


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


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
