"""Provider-independent report values."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Period:
    kind: str
    start: date
    end: date


@dataclass(frozen=True)
class ReportItem:
    id: str
    title: str
    completed_on: date
    url: str
    section: str
    details: str = ""
    recorded_at: str = ""
