"""Load settings explicitly; environment variables override .env."""

import json
import os
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

import msgspec

NonEmptyString = Annotated[str, msgspec.Meta(min_length=1)]
ClockTime = Annotated[str, msgspec.Meta(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")]
Weekday = Annotated[int, msgspec.Meta(ge=0, le=6)]
NonNegativeBuffer = Annotated[int, msgspec.Meta(ge=0, le=60)]
ReminderHorizon = Annotated[int, msgspec.Meta(ge=0, le=365)]
PositiveInt = Annotated[int, msgspec.Meta(ge=1)]
ReasoningEffort = Literal["low", "medium", "high"]


class _CompletionSource(msgspec.Struct):
    id: NonEmptyString
    name: NonEmptyString
    checkbox: NonEmptyString
    completed_on: NonEmptyString


class _ReportSource(msgspec.Struct):
    id: NonEmptyString
    name: NonEmptyString
    date_property: NonEmptyString
    title_property: NonEmptyString
    checkbox_properties: list[str] = msgspec.field(default_factory=list)
    detail_properties: list[str] = msgspec.field(default_factory=list)
    excluded_status_values: list[str] = msgspec.field(default_factory=list)


class _NotionTasks(msgspec.Struct):
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


class _Inbox(msgspec.Struct):
    data_source_id: NonEmptyString | None = None
    title_property: NonEmptyString | None = None


class _CalendarPlanning(msgspec.Struct):
    calendar_id: NonEmptyString = "primary"
    day_start: ClockTime = "08:00"
    day_end: ClockTime = "22:00"
    buffer_minutes: NonNegativeBuffer = 15


class _Weather(msgspec.Struct):
    enabled: bool = False
    time: ClockTime = "06:00"


class _Reminders(msgspec.Struct):
    enabled: bool = True
    weekdays: list[Weekday] = msgspec.field(default_factory=lambda: list(range(7)))
    time: ClockTime = "06:00"
    days_ahead: ReminderHorizon = 7
    notion_tasks: _NotionTasks | None = None


class _ReportAI(msgspec.Struct):
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


class _Reports(msgspec.Struct):
    enabled: bool = False
    hour: Annotated[int, msgspec.Meta(ge=0, le=23)] = 9
    minute: Annotated[int, msgspec.Meta(ge=0, le=59)] = 0
    weekly_weekday: Weekday = 0
    sources: list[_ReportSource] = msgspec.field(default_factory=list)
    ai: _ReportAI = msgspec.field(default_factory=_ReportAI)


class _Operations(msgspec.Struct):
    backup_retention: PositiveInt = 14
    alert_cooldown_seconds: PositiveInt = 3600


class _Configuration(msgspec.Struct):
    completion_sources: list[_CompletionSource] | None = None
    reports: _Reports = msgspec.field(default_factory=_Reports)
    operations: _Operations = msgspec.field(default_factory=_Operations)
    reminders: _Reminders = msgspec.field(default_factory=_Reminders)
    inbox: _Inbox = msgspec.field(default_factory=_Inbox)
    calendar_planning: _CalendarPlanning = msgspec.field(default_factory=_CalendarPlanning)
    weather: _Weather = msgspec.field(default_factory=_Weather)


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


@dataclass(frozen=True)
class _RuntimeValues:
    notion_token: str = field(repr=False)
    telegram_token: str = field(repr=False)
    timezone: ZoneInfo
    poll_seconds: int
    telegram_chat_id: str = field(repr=False)
    groq_api_key: str = field(repr=False)
    groq_model: str
    groq_api_keys: tuple[str, ...] = field(repr=False)
    groq_planning_api_key: str = field(repr=False)
    groq_planning_model: str
    calendar_secret_file: Path | None = field(repr=False)
    weather_city: str = field(repr=False)


def load_settings(root: Path) -> Settings:
    environment = _read_environment(root)
    config_data, configuration = _read_configuration(root)
    _validate_configuration(config_data, configuration, environment)
    values = _resolve_runtime_values(root, config_data, configuration, environment)
    return Settings(
        root=root,
        notion_token=values.notion_token,
        telegram_token=values.telegram_token,
        timezone=values.timezone,
        poll_seconds=values.poll_seconds,
        config=config_data,
        telegram_chat_id=values.telegram_chat_id,
        groq_api_key=values.groq_api_key,
        groq_model=values.groq_model,
        groq_api_keys=values.groq_api_keys,
        groq_planning_api_key=values.groq_planning_api_key,
        groq_planning_model=values.groq_planning_model,
        google_calendar_client_secret_file=values.calendar_secret_file,
        weather_city=values.weather_city,
    )


def _read_environment(root: Path) -> dict[str, str]:
    environment: dict[str, str] = {}
    env_file = root / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                key, value = line.split("=", 1)
                environment[key.strip()] = value.strip()
    environment.update(os.environ)
    return environment


def _read_configuration(root: Path) -> tuple[dict[str, Any], _Configuration]:
    config_data = json.loads((root / "config" / "settings.json").read_text())
    if not isinstance(config_data, dict):
        raise ValueError("Настройки должны быть объектом JSON")
    try:
        configuration = msgspec.convert(config_data, type=_Configuration, strict=True)
    except msgspec.ValidationError as error:
        raise ValueError(str(error)) from None
    return config_data, configuration


def _validate_configuration(
    config_data: dict[str, Any],
    configuration: _Configuration,
    environment: dict[str, str],
) -> None:
    if not environment.get("NOTION_TOKEN", ""):
        raise ValueError("NOTION_TOKEN не задан")

    sources = configuration.completion_sources
    if not sources:
        raise ValueError("Нет настроенных баз completion_sources")
    identities = [(source.id, source.checkbox) for source in sources]
    if len(set(identities)) != len(identities):
        raise ValueError("Повторная настройка одного чекбокса")

    planning = configuration.calendar_planning
    if config_data.get("calendar_planning") and time.fromisoformat(
        planning.day_start
    ) >= time.fromisoformat(planning.day_end):
        raise ValueError("calendar_planning.day_start должен быть раньше day_end")

    if config_data.get("inbox") and (
        not configuration.inbox.data_source_id or not configuration.inbox.title_property
    ):
        raise ValueError("Некорректные настройки inbox")

    reminders_enabled = bool(config_data.get("reminders"))
    reminders = configuration.reminders
    if reminders_enabled and len(set(reminders.weekdays)) != len(reminders.weekdays):
        raise ValueError("reminders.weekdays должен содержать уникальные дни от 0 до 6")
    if reminders_enabled and reminders.notion_tasks is None:
        raise ValueError("Некорректные настройки reminders.notion_tasks")

    if configuration.reports.enabled and not configuration.reports.sources:
        raise ValueError("Отчёты требуют sources")

    if configuration.weather.enabled and not environment.get("WEATHER_CITY", "").strip():
        raise ValueError("WEATHER_CITY не задан для включённой погоды")


def _resolve_runtime_values(
    root: Path,
    config_data: dict[str, Any],
    configuration: _Configuration,
    environment: dict[str, str],
) -> _RuntimeValues:
    chat_id = environment.get("TELEGRAM_CHAT_ID", "").strip()
    if chat_id:
        try:
            numeric_id = int(chat_id)
            if numeric_id <= 0:
                raise ValueError
            chat_id = str(numeric_id)
        except ValueError:
            raise ValueError("TELEGRAM_CHAT_ID должен быть положительным ID личного чата") from None

    groq_api_keys = tuple(
        dict.fromkeys(
            key
            for key in (
                environment.get("GROQ_API_KEY", "").strip(),
                *(part.strip() for part in environment.get("GROQ_API_KEYS", "").split(",")),
            )
            if key
        )
    )
    if len(groq_api_keys) > 10:
        raise ValueError("GROQ_API_KEYS допускает максимум 10 уникальных ключей")
    groq_api_key = groq_api_keys[0] if groq_api_keys else ""
    if configuration.reports.enabled and not groq_api_key:
        raise ValueError("Включённые отчёты требуют GROQ_API_KEY")
    groq_model = environment.get("GROQ_MODEL", "openai/gpt-oss-120b").strip()
    if groq_api_key and not groq_model:
        raise ValueError("GROQ_MODEL не задан")

    groq_planning_api_key = environment.get("GROQ_PLANNING_API_KEY", "").strip()
    groq_planning_model = environment.get("GROQ_PLANNING_MODEL", "openai/gpt-oss-20b").strip()
    if groq_planning_api_key and not groq_planning_model:
        raise ValueError("GROQ_PLANNING_MODEL не задан")

    calendar_secret = environment.get("GOOGLE_CALENDAR_CLIENT_SECRET_FILE", "").strip()
    calendar_secret_file: Path | None
    if calendar_secret:
        calendar_secret_file = Path(calendar_secret).expanduser()
        if not calendar_secret_file.is_absolute():
            calendar_secret_file = root / calendar_secret_file
    elif config_data.get("calendar_planning"):
        matches = sorted(root.glob("client_secret_*.json"))
        calendar_secret_file = matches[0] if len(matches) == 1 else None
    else:
        calendar_secret_file = None

    return _RuntimeValues(
        notion_token=environment["NOTION_TOKEN"],
        telegram_token=environment.get("TELEGRAM_BOT_TOKEN", ""),
        timezone=ZoneInfo(environment.get("TIMEZONE", "Asia/Bishkek")),
        poll_seconds=max(5, int(environment.get("POLL_SECONDS", "30"))),
        telegram_chat_id=chat_id,
        groq_api_key=groq_api_key,
        groq_model=groq_model,
        groq_api_keys=groq_api_keys,
        groq_planning_api_key=groq_planning_api_key,
        groq_planning_model=groq_planning_model,
        calendar_secret_file=calendar_secret_file,
        weather_city=environment.get("WEATHER_CITY", "").strip(),
    )
