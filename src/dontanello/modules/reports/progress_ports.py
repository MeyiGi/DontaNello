"""Narrow boundaries for the shared progress pipeline."""

from collections.abc import Sequence
from typing import Protocol

from .models import Period
from .progress_models import (
    Analysis,
    Evidence,
    HistoricalContext,
    ProgressDocument,
    ReportSection,
    StrategySpec,
)


class ProgressAnalyzer(Protocol):
    def analyze(
        self,
        period: Period,
        evidence: Sequence[Evidence],
        history: HistoricalContext,
        strategy: StrategySpec,
    ) -> Analysis: ...


class ProgressArchive(Protocol):
    def get(self, scope: str, period: Period) -> ProgressDocument | None: ...
    def context(self, scope: str, period: Period) -> HistoricalContext: ...
    def save(self, scope: str, document: ProgressDocument) -> ProgressDocument: ...


class ReportStrategy(Protocol):
    def specification(self) -> StrategySpec: ...
    def title(self, period: Period) -> str: ...
    def sections(self, period: Period, analysis: Analysis) -> tuple[ReportSection, ...]: ...
