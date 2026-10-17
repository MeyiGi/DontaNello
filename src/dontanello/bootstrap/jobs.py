"""Worker jobs and their schedules. State recovery stays in Runtime.jobs()."""

from __future__ import annotations

from datetime import time
from typing import TYPE_CHECKING

from dontanello.entrypoints.telegram import TelegramCommands
from dontanello.entrypoints.worker import Job
from dontanello.integrations.telegram.client import TelegramClient
from dontanello.integrations.weather.open_meteo import OpenMeteoClient
from dontanello.modules.completion import CompletionTracker
from dontanello.modules.completion.adapters.json_state import JsonCheckboxState
from dontanello.modules.operations import BackupService, ErrorMonitor
from dontanello.modules.operations.adapters.filesystem_backups import FileBackupStore
from dontanello.modules.operations.adapters.json_alerts import JsonAlertState
from dontanello.modules.operations.adapters.serialized_monitor import SerializedMonitor
from dontanello.modules.operations.adapters.telegram import TelegramAlertSender
from dontanello.modules.reminders import ReminderApplication, TaskDigestSettings
from dontanello.modules.reminders.adapters.sqlite import SQLiteReminderRepository
from dontanello.modules.reminders.adapters.telegram import TelegramReminderSender
from dontanello.modules.reminders.weekly_routine import WeeklyRoutine
from dontanello.modules.reports import DeliveryService, ScheduledReports
from dontanello.modules.reports.adapters.sqlite_delivery import SQLiteDeliveryStore
from dontanello.modules.reports.adapters.telegram import TelegramReportSender
from dontanello.modules.weather import WeatherApplication
from dontanello.modules.weather.adapters.open_meteo import OpenMeteoWeatherProvider
from dontanello.modules.weather.adapters.sqlite import SQLiteWeatherRepository
from dontanello.modules.weather.adapters.telegram import TelegramWeatherSender
from dontanello.platform.clock import LocalClock
from dontanello.platform.telegram_cursor import TelegramCursor

if TYPE_CHECKING:
    from collections.abc import Callable

    from dontanello.bootstrap import Runtime


_WEEKDAYS = ("понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье")


def register_jobs(runtime: Runtime) -> list[Job]:
    """All jobs in launch order; Telegram work is optional."""
    jobs = _completion_jobs(runtime)
    jobs.append(_backup_job(runtime))

    settings = runtime.settings
    if not settings.telegram_token or not settings.telegram_chat_id:
        return jobs

    telegram = TelegramClient(settings.telegram_token)
    if runtime.task_deadline_source is not None:
        jobs.extend(_reminder_jobs(runtime, telegram))
    jobs.extend(_learning_routine_jobs(runtime, telegram))
    jobs.extend(_telegram_jobs(runtime, telegram))
    return jobs


def _completion_jobs(runtime: Runtime) -> list[Job]:
    state = JsonCheckboxState(runtime.settings.root / "state" / "checkboxes.json")
    clock = LocalClock(runtime.settings.timezone)
    return [
        Job(
            f"completion_{index}",
            CompletionTracker(source, state, clock).run,
            group="completion",
        )
        for index, source in enumerate(runtime.sources)
    ]


def _backup_job(runtime: Runtime) -> Job:
    retention = runtime.settings.config.get("operations", {}).get("backup_retention", 14)
    backup = BackupService(FileBackupStore(runtime.settings.root), retention)
    return Job("backups", lambda: backup.run(runtime.now()), group="backups", interval_seconds=3600)


def _reminder_jobs(runtime: Runtime, telegram: TelegramClient) -> list[Job]:
    source = runtime.task_deadline_source
    assert source is not None
    settings = runtime.settings
    config = settings.config["reminders"]
    defaults = TaskDigestSettings(
        enabled=config.get("enabled", True),
        weekdays=tuple(config.get("weekdays", range(7))),
        send_time=time.fromisoformat(config.get("time", "06:00")),
        days_ahead=config.get("days_ahead", 7),
    )
    reminders = ReminderApplication(
        SQLiteReminderRepository(settings.root / "state" / "reminders.sqlite3"),
        source,
        TelegramReminderSender(telegram),
        settings.telegram_chat_id,
        defaults,
    )
    reminders.recover_inflight()
    runtime.reminders_app = reminders
    return [
        Job(
            "task_digest",
            lambda: reminders.run_task_digest(runtime.now()),
            group="task_digest",
            interval_seconds=60,
        ),
        Job(
            "personal_reminders",
            lambda: reminders.run_due_personal(runtime.now()),
            group="personal_reminders",
            interval_seconds=15,
        ),
    ]


def _learning_routine_jobs(runtime: Runtime, telegram: TelegramClient) -> list[Job]:
    """The personal learning plan is independent of Notion task availability."""
    settings = runtime.settings
    config = settings.config.get("learning_routine", {})
    if not config.get("enabled", False):
        return []
    routine = WeeklyRoutine(
        SQLiteReminderRepository(settings.root / "state" / "reminders.sqlite3"),
        TelegramReminderSender(telegram),
        settings.telegram_chat_id,
        settings.timezone,
        time.fromisoformat(config.get("time", "06:30")),
        tuple(tuple(day) for day in config["days"]),
    )
    return [
        Job(
            "learning_routine",
            lambda: routine.run(runtime.now()),
            group="learning_routine",
            interval_seconds=60,
        )
    ]


def _telegram_jobs(runtime: Runtime, telegram: TelegramClient) -> list[Job]:
    """Connect Telegram delivery, optional weather, commands, and polling."""
    settings = runtime.settings
    operations = settings.config.get("operations", {})
    runtime.monitor = SerializedMonitor(
        ErrorMonitor(
            JsonAlertState(settings.root / "state" / "alerts.json"),
            TelegramAlertSender(telegram, settings.telegram_chat_id),
            operations.get("alert_cooldown_seconds", 3600),
        )
    )

    journal = SQLiteDeliveryStore(settings.root / "state" / "reports.sqlite3")
    delivery = DeliveryService(journal, TelegramReportSender(telegram), settings.telegram_chat_id)
    reports = settings.config.get("reports", {})
    hour, minute = reports.get("hour", 9), reports.get("minute", 0)
    weekday = reports.get("weekly_weekday", 0)
    scheduler = ScheduledReports(
        delivery, runtime.report, journal.active_since(runtime.now()), hour, minute, weekday
    )
    runtime.weather_app = _weather(runtime, telegram)

    commands = TelegramCommands(
        telegram,
        TelegramCursor(settings.root / "state" / "telegram_cursor.json"),
        settings.telegram_chat_id,
        delivery,
        runtime.report,
        runtime.now,
        _status_message(runtime, journal, reports, hour, minute, weekday),
        full_report=lambda period: runtime.report(period, full=True),
        weekly_weekday=weekday,
        reminders=runtime.reminders_app,
        inbox=runtime.inbox_app,
        planning=runtime.planning_app,
        task_capture=runtime.task_capture_app,
        message_intent=runtime.message_intent_interpreter,
        weather=runtime.weather_app,
    )

    jobs = [
        Job(
            "telegram_menu",
            _menu_initializer(runtime, telegram),
            group="telegram_menu",
            interval_seconds=60,
        ),
        Job("telegram", commands.run, group="telegram", interval_seconds=1),
    ]
    weather = runtime.weather_app
    if weather is not None:
        jobs.append(
            Job(
                "weather",
                lambda: weather.run_morning(runtime.now()),
                group="weather",
                interval_seconds=60,
            )
        )
    if reports.get("enabled", False):
        jobs.append(
            Job(
                "reports",
                lambda: scheduler.run(runtime.now()),
                group="reports",
                interval_seconds=60,
            )
        )
    jobs.append(Job("alerts", runtime.flush_outcomes, group="alerts", interval_seconds=1))
    return jobs


def _weather(runtime: Runtime, telegram: TelegramClient) -> WeatherApplication | None:
    settings = runtime.settings
    config = settings.config.get("weather", {})
    if not config.get("enabled", False):
        return None
    return WeatherApplication(
        OpenMeteoWeatherProvider(OpenMeteoClient()),
        SQLiteWeatherRepository(settings.root / "state" / "weather.sqlite3"),
        TelegramWeatherSender(telegram),
        settings.weather_city,
        settings.telegram_chat_id,
        settings.timezone,
        settings.timezone.key,
        time.fromisoformat(config.get("time", "06:00")),
    )


def _status_message(
    runtime: Runtime,
    journal: SQLiteDeliveryStore,
    reports: dict,
    hour: int,
    minute: int,
    weekday: int,
) -> Callable[[], str]:
    def status() -> str:
        counts = journal.status()
        schedule = (
            f"{_WEEKDAYS[weekday]} и первое число, "
            f"{hour:02}:{minute:02} ({runtime.settings.timezone.key})"
        )
        enabled = "включены" if reports.get("enabled", False) else "выключены"
        return (
            f"Dontanello работает.\nОтчёты {enabled}: {schedule}."
            "\nРезервная копия состояния: ежедневно."
            f"\nЧастей отчётов доставлено: {counts['sent']}; ожидают: {counts['pending']};"
            f" требуют проверки: {counts['uncertain']}."
        )

    return status


def _menu_initializer(runtime: Runtime, telegram: TelegramClient) -> Callable[[], int]:
    """Register Telegram commands only once for each worker start."""
    menu = [
        {"command": "week", "description": "Обзор за прошлую неделю"},
        {"command": "month", "description": "Обзор за прошлый месяц"},
        {"command": "help", "description": "Показать команды DontaNello"},
        {"command": "status", "description": "Состояние бота"},
    ]
    if runtime.reminders_app:
        menu.extend(
            [
                {"command": "tasksettings", "description": "Настроить дедлайны"},
                {"command": "reminders", "description": "Мои напоминания"},
                {"command": "cancelreminder", "description": "Отменить напоминание"},
            ]
        )
    if runtime.weather_app:
        menu.append({"command": "weather", "description": "Прогноз погоды на сегодня"})
    if runtime.inbox_app:
        menu.append({"command": "inbox", "description": "Записать идею в Notion Inbox"})

    configured = False

    def configure_menu() -> int:
        nonlocal configured
        if not configured:
            telegram.set_my_commands(menu)
            configured = True
        return 0

    return configure_menu
