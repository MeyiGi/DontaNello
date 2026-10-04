"""Typed requests and actions for personal calendar planning."""

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class PlanRequest:
    day: date
    title: str
    duration_minutes: int
    fixed_slot: "TimeSlot | None" = None


@dataclass(frozen=True)
class PendingPlanIntent:
    update_id: int
    request_text: str
    title: str
    day: date
    created_at: datetime


@dataclass(frozen=True)
class TimeSlot:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class CalendarEvent:
    id: str
    title: str
    start: datetime
    end: datetime


@dataclass(frozen=True)
class PlanProposal:
    id: str
    update_id: int
    request_text: str
    title: str
    day: date
    duration_minutes: int
    options: tuple[TimeSlot, ...]
    selected_index: int | None
    status: str
    event_id: str = ""


@dataclass(frozen=True)
class InlineButton:
    label: str
    callback_data: str


@dataclass(frozen=True)
class PlannerResponse:
    text: str
    button_rows: tuple[tuple[InlineButton, ...], ...] = ()


class CalendarUnavailable(RuntimeError):
    """Calendar access is unavailable or the remote result is uncertain."""


class CalendarNotConnected(CalendarUnavailable):
    """The user has not completed Google Calendar authorization."""


def plan_event_id(proposal_id: str) -> str:
    """Google event IDs are stable so a retry cannot create a second event."""
    return "dn" + proposal_id[:32]


def format_slot(slot: TimeSlot) -> str:
    return f"{slot.start:%H:%M}–{slot.end:%H:%M}"
