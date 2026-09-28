"""Read contract required by report generation."""

from collections.abc import Iterable
from typing import Protocol

from .models import Period, ReportItem


class ReportSource(Protocol):
    def items(self, period: Period) -> Iterable[ReportItem]: ...
