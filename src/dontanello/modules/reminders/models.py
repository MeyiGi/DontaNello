"""Typed values owned by the reminder capability."""

from dataclasses import dataclass
from datetime import date, datetime, time


@dataclass(frozen=True)
class TaskDeadline:
    id: str
    title: str
    due_date: date
    url: str = ""
    completed: bool = False
    cancelled: bool = False


@dataclass(frozen=True)
class TaskDigestSettings:
    enabled: bool = True
    weekdays: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6)
    send_time: time = time(6, 0)
    days_ahead: int = 7


@dataclass(frozen=True)
class PersonalReminder:
    id: int
    source_update_id: int
    text: str
    due_at: datetime
    status: str = "pending"


@dataclass(frozen=True)
class ParsedReminder:
    text: str
    due_at: datetime


@dataclass(frozen=True)
class ReminderParseError:
    message: str


class ReminderSendRejected(RuntimeError):
    """Telegram explicitly rejected a reminder delivery."""


class ReminderSendUncertain(RuntimeError):
    """The send outcome is unknown; an automatic retry may duplicate it."""
