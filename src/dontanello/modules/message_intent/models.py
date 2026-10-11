"""Validated intent suggestions for private Telegram messages."""

from dataclasses import dataclass
from datetime import date, time
from typing import Literal

IntentDestination = Literal["inbox", "task", "calendar", "reminder", "clarify", "other"]
IntentConfidence = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class CalendarIntent:
    day: date
    duration_minutes: int | None = None
    start_time: time | None = None
    end_time: time | None = None


@dataclass(frozen=True)
class ReminderIntent:
    day: date | None = None
    clock: time | None = None
    daypart: str | None = None
    delay_minutes: int | None = None


@dataclass(frozen=True)
class MessageIntent:
    destination: IntentDestination
    confidence: IntentConfidence
    title: str = ""
    due_date: date | None = None
    normalized_text: str = ""
    calendar: CalendarIntent | None = None
    reminder: ReminderIntent | None = None
