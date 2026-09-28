"""Calendar scheduling for weekly and monthly reports."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from .application import previous_month, previous_week
from .delivery import DeliveryService
from .models import Period


class ScheduledReportError(RuntimeError):
    """One or more reports failed; the message intentionally omits provider data."""

    def __init__(self, failed_kinds: list[str]):
        self.failed_kinds = tuple(failed_kinds)
        super().__init__("report jobs failed: " + ", ".join(failed_kinds))


@dataclass
class ScheduledReports:
    delivery: DeliveryService
    build: Callable[[Period], str]
    activated_on: date
    hour: int = 9
    minute: int = 0

    def run(self, now: datetime) -> int:
        if not 0 <= self.hour <= 23 or not 0 <= self.minute <= 59:
            raise ValueError("report schedule time must be a valid local clock time")
        total_sent = 0
        failures: list[str] = []
        for kind, due, period in self._latest_due_periods(now):
            if due.date() < self.activated_on:
                continue
            key = f"{kind}:{period.start.isoformat()}:{period.end.isoformat()}"
            try:
                if not self.delivery.needs_delivery(key, now):
                    if self.delivery.has_uncertainty(key):
                        failures.append(kind)
                    continue
                # A pending journal row ignores this freshly built text and resumes its
                # durable first snapshot. A build failure cannot alter delivery state.
                report = self.build(period)
                total_sent += self.delivery.deliver(key, report, now)
            except Exception:
                failures.append(kind)
        if failures:
            raise ScheduledReportError(failures)
        return total_sent

    def _latest_due_periods(self, now: datetime) -> list[tuple[str, datetime, Period]]:
        scheduled_time = time(self.hour, self.minute)
        tz = now.tzinfo

        monday = now.date() - timedelta(days=now.date().weekday())
        weekly_due = datetime.combine(monday, scheduled_time, tzinfo=tz)
        if weekly_due > now:
            monday -= timedelta(days=7)
            weekly_due = datetime.combine(monday, scheduled_time, tzinfo=tz)

        month_start = now.date().replace(day=1)
        monthly_due = datetime.combine(month_start, scheduled_time, tzinfo=tz)
        if monthly_due > now:
            prior = month_start - timedelta(days=1)
            month_start = prior.replace(day=1)
            monthly_due = datetime.combine(month_start, scheduled_time, tzinfo=tz)

        return [
            ("week", weekly_due, previous_week(monday)),
            ("month", monthly_due, previous_month(month_start)),
        ]
