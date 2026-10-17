"""Typed settings schema and runtime settings model."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

import msgspec

# Reusable field constraints.
NonEmptyString = Annotated[str, msgspec.Meta(min_length=1)]
ClockTime = Annotated[str, msgspec.Meta(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")]
Weekday = Annotated[int, msgspec.Meta(ge=0, le=6)]
NonNegativeBuffer = Annotated[int, msgspec.Meta(ge=0, le=60)]
ReminderHorizon = Annotated[int, msgspec.Meta(ge=0, le=365)]
PositiveInt = Annotated[int, msgspec.Meta(ge=1)]
ReasoningEffort = Literal["low", "medium", "high"]


# JSON configuration schema.
class CompletionSource(msgspec.Struct):
    id: NonEmptyString
    name: NonEmptyString
    checkbox: NonEmptyString
    completed_on: NonEmptyString


class ReportSource(msgspec.Struct):
    id: NonEmptyString
    name: NonEmptyString
    date_property: NonEmptyString
    title_property: NonEmptyString
    checkbox_properties: list[str] = msgspec.field(default_factory=list)
    detail_properties: list[str] = msgspec.field(default_factory=list)
    excluded_status_values: list[str] = msgspec.field(default_factory=list)


class NotionTasks(msgspec.Struct):
    source_id: NonEmptyString
    title_property: NonEmptyString
    due_property: NonEmptyString
    next_due_property: NonEmptyString
    completed_property: NonEmptyString | None = None
    checkbox_properties: list[str] = msgspec.field(default_factory=list)
    status_property: NonEmptyString | None = None
    context_property: NonEmptyString | None = None
    excluded_status_values: list[str] = msgspec.field(default_factory=list)
    excluded_context_values: list[str] = msgspec.field(default_factory=list)


class Inbox(msgspec.Struct):
    data_source_id: NonEmptyString | None = None
    title_property: NonEmptyString | None = None


class CalendarPlanning(msgspec.Struct):
    calendar_id: NonEmptyString = "primary"
    day_start: ClockTime = "08:00"
    day_end: ClockTime = "22:00"
    buffer_minutes: NonNegativeBuffer = 15


class Weather(msgspec.Struct):
    enabled: bool = False
    time: ClockTime = "06:00"


class Reminders(msgspec.Struct):
    enabled: bool = True
    weekdays: list[Weekday] = msgspec.field(default_factory=lambda: list(range(7)))
    time: ClockTime = "06:00"
    days_ahead: ReminderHorizon = 7
    notion_tasks: NotionTasks | None = None


class ReportAI(msgspec.Struct):
    max_rounds: Annotated[int, msgspec.Meta(ge=1, le=3)] = 3
    max_batch_chars: Annotated[int, msgspec.Meta(ge=1, le=20_000)] = 10_000
    max_batches: Annotated[int, msgspec.Meta(ge=1, le=48)] = 24
    max_requests: Annotated[int, msgspec.Meta(ge=1, le=96)] = 48
    max_output_tokens: Annotated[int, msgspec.Meta(ge=1, le=8_000)] = 4_500
    max_input_chars: Annotated[int, msgspec.Meta(ge=1, le=500_000)] = 160_000
    max_history_records: Annotated[int, msgspec.Meta(ge=1, le=500)] = 80
    timeout_seconds: Annotated[int, msgspec.Meta(ge=1, le=600)] = 180
    weekly_reasoning: ReasoningEffort = "medium"
    monthly_reasoning: ReasoningEffort = "high"


class LearningRoutine(msgspec.Struct):
    enabled: bool = False
    time: ClockTime = "06:30"
    # Monday through Sunday. Each day's activities are displayed without transformation.
    days: list[list[NonEmptyString]] = msgspec.field(default_factory=list)


class Reports(msgspec.Struct):
    enabled: bool = False
    hour: Annotated[int, msgspec.Meta(ge=0, le=23)] = 9
    minute: Annotated[int, msgspec.Meta(ge=0, le=59)] = 0
    weekly_weekday: Weekday = 0
    sources: list[ReportSource] = msgspec.field(default_factory=list)
    ai: ReportAI = msgspec.field(default_factory=ReportAI)


class Operations(msgspec.Struct):
    backup_retention: PositiveInt = 14
    alert_cooldown_seconds: PositiveInt = 3600


class Configuration(msgspec.Struct):
    completion_sources: list[CompletionSource] | None = None
    reports: Reports = msgspec.field(default_factory=Reports)
    operations: Operations = msgspec.field(default_factory=Operations)
    # None means the optional section was omitted from settings.json.
    reminders: Reminders | None = None
    inbox: Inbox | None = None
    calendar_planning: CalendarPlanning | None = None
    weather: Weather = msgspec.field(default_factory=Weather)
    learning_routine: LearningRoutine = msgspec.field(default_factory=LearningRoutine)


@dataclass(frozen=True)
class Settings:
    root: Path
    notion_token: str = field(repr=False)
    telegram_token: str = field(repr=False)
    timezone: ZoneInfo
    poll_seconds: int
    config: dict[str, Any]
    telegram_chat_id: str = field(default="", repr=False)
    groq_api_key: str = field(default="", repr=False)
    groq_model: str = "openai/gpt-oss-120b"
    groq_api_keys: tuple[str, ...] = field(default=(), repr=False)
    groq_planning_api_key: str = field(default="", repr=False)
    groq_planning_model: str = "openai/gpt-oss-20b"
    google_calendar_client_secret_file: Path | None = field(default=None, repr=False)
    weather_city: str = field(default="", repr=False)
