"""Cross-field configuration checks, organized by section."""

from .schema import (
    CalendarPlanning,
    CompletionSource,
    Configuration,
    Inbox,
    LearningRoutine,
    Reminders,
    Reports,
    Weather,
)


# Each validator is responsible for one configuration section.
def _validate(config: Configuration, env: dict[str, str]) -> None:
    if not env.get("NOTION_TOKEN"):
        raise ValueError("NOTION_TOKEN не задан")

    _validate_sources(config.completion_sources)
    _validate_calendar(config.calendar_planning)
    _validate_inbox(config.inbox)
    _validate_reminders(config.reminders)
    _validate_learning_routine(config.learning_routine)
    _validate_reports(config.reports, env)
    _validate_weather(config.weather, env)


def _validate_sources(sources: list[CompletionSource] | None) -> None:
    if not sources:
        raise ValueError("Нет настроенных баз completion_sources")

    identities = [(source.id, source.checkbox) for source in sources]
    if len(identities) != len(set(identities)):
        raise ValueError("Повторная настройка одного чекбокса")


def _validate_calendar(planning: CalendarPlanning | None) -> None:
    # ClockTime has a fixed HH:MM format, so lexical comparison is safe.
    if planning is not None and planning.day_start >= planning.day_end:
        raise ValueError("calendar_planning.day_start должен быть раньше day_end")


def _validate_inbox(inbox: Inbox | None) -> None:
    if inbox is not None and (not inbox.data_source_id or not inbox.title_property):
        raise ValueError("Некорректные настройки inbox")


def _validate_reminders(reminders: Reminders | None) -> None:
    if reminders is None or not reminders.enabled:
        return

    if len(reminders.weekdays) != len(set(reminders.weekdays)):
        raise ValueError("reminders.weekdays должен содержать уникальные дни от 0 до 6")
    if reminders.notion_tasks is None:
        raise ValueError("Некорректные настройки reminders.notion_tasks")


def _validate_learning_routine(routine: LearningRoutine) -> None:
    if not routine.enabled:
        return
    if len(routine.days) != 7 or any(not day for day in routine.days):
        raise ValueError("learning_routine.days должен содержать 7 непустых дней (пн–вс)")
    if routine.time >= "12:00":
        raise ValueError("learning_routine.time должен быть раньше 12:00")


def _validate_reports(reports: Reports, env: dict[str, str]) -> None:
    if not reports.enabled:
        return

    if not reports.sources:
        raise ValueError("Отчёты требуют sources")
    if not _groq_keys(env):
        raise ValueError("Включённые отчёты требуют GROQ_API_KEY")


def _validate_weather(weather: Weather, env: dict[str, str]) -> None:
    if weather.enabled and not env.get("WEATHER_CITY", "").strip():
        raise ValueError("WEATHER_CITY не задан для включённой погоды")


def _groq_keys(env: dict[str, str]) -> tuple[str, ...]:
    primary_key = env.get("GROQ_API_KEY", "").strip()
    extra_keys = (key.strip() for key in env.get("GROQ_API_KEYS", "").split(","))
    keys = tuple(dict.fromkeys(key for key in (primary_key, *extra_keys) if key))

    if len(keys) > 10:
        raise ValueError("GROQ_API_KEYS допускает максимум 10 уникальных ключей")
    return keys
