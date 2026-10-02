"""Evidence and structured reflection contracts owned by reports."""

from dataclasses import dataclass
from datetime import date, datetime

from .models import Period


@dataclass(frozen=True)
class Evidence:
    id: str
    source_id: str
    project: str
    occurred_on: date
    recorded_at: str
    text: str
    source_kind: str
    url: str = ""


@dataclass(frozen=True)
class Citation:
    evidence_id: str
    quote: str


@dataclass(frozen=True)
class Finding:
    kind: str
    project: str
    text: str
    citations: tuple[Citation, ...]
    before: str = ""
    action: str = ""
    after: str = ""
    before_ids: tuple[str, ...] = ()
    after_ids: tuple[str, ...] = ()
    area: str = ""
    status: str = ""


@dataclass(frozen=True)
class AnalysisMetrics:
    model: str = ""
    reasoning: str = ""
    api_requests: int = 0
    subagents: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0


@dataclass(frozen=True)
class Analysis:
    findings: tuple[Finding, ...] = ()
    notices: tuple[str, ...] = ()
    metrics: AnalysisMetrics | None = None


@dataclass(frozen=True)
class ReportSection:
    key: str
    title: str
    findings: tuple[Finding, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class ProgressDocument:
    period: Period
    title: str
    sections: tuple[ReportSection, ...]
    evidence: tuple[Evidence, ...]
    generated_at: datetime
    strategy_version: str = "1"
    schema_version: int = 1
    analysis_metrics: AnalysisMetrics | None = None


@dataclass(frozen=True)
class HistoricalContext:
    evidence: tuple[Evidence, ...] = ()
    reports: tuple[ProgressDocument, ...] = ()


@dataclass(frozen=True)
class SectionSpec:
    key: str
    title: str
    kinds: tuple[str, ...]
    limit: int
    instructions: str
    optional: bool = False


@dataclass(frozen=True)
class StrategySpec:
    kind: str
    instructions: str
    sections: tuple[SectionSpec, ...]
    max_words: int
    max_chars: int
