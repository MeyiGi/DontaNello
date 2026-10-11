"""Build optional applications from settings; no jobs or external writes here."""

from __future__ import annotations

from datetime import time
from typing import TYPE_CHECKING

from dontanello.integrations.google_calendar.client import GoogleCalendarClient
from dontanello.integrations.groq.client import GroqClient
from dontanello.integrations.notion.client import NotionClient
from dontanello.modules.calendar_planning import CalendarPlanningApplication
from dontanello.modules.calendar_planning.adapters.google_calendar import GoogleCalendarAdapter
from dontanello.modules.calendar_planning.adapters.groq import GroqPlanningInterpreter
from dontanello.modules.calendar_planning.adapters.sqlite import SQLitePlanningRepository
from dontanello.modules.completion.adapters.notion import (
    NotionCompletionConfig,
    NotionCompletionSource,
)
from dontanello.modules.inbox import InboxCaptureApplication
from dontanello.modules.inbox.adapters.notion import NotionInboxConfig, NotionInboxWriter
from dontanello.modules.inbox.adapters.sqlite import SQLiteInboxCaptureStore
from dontanello.modules.message_intent.adapters.groq import GroqMessageIntentInterpreter
from dontanello.modules.reminders.adapters.notion import NotionTaskConfig, NotionTaskDeadlineSource
from dontanello.modules.reports import ProgressReports
from dontanello.modules.reports.adapters.groq_progress import GroqProgressAnalyzer
from dontanello.modules.reports.adapters.notion import NotionReportConfig, NotionReportSource
from dontanello.modules.reports.adapters.sqlite_progress import SQLiteProgressArchive
from dontanello.modules.task_capture import TaskCaptureApplication
from dontanello.modules.task_capture.adapters.notion import NotionTaskWriter, NotionTaskWriterConfig
from dontanello.modules.task_capture.adapters.sqlite import SQLiteTaskCaptureRepository
from dontanello.platform.settings import Settings

if TYPE_CHECKING:
    from dontanello.bootstrap import Runtime


def completion_sources(settings: Settings, notion: NotionClient) -> list[NotionCompletionSource]:
    return [
        NotionCompletionSource(notion, NotionCompletionConfig(**source))
        for source in settings.config["completion_sources"]
    ]


def report_sources(settings: Settings, notion: NotionClient) -> list[NotionReportSource]:
    return [
        NotionReportSource(notion, NotionReportConfig(**source), settings.timezone)
        for source in settings.config.get("reports", {}).get("sources", [])
    ]


def deadline_source(settings: Settings, notion: NotionClient) -> NotionTaskDeadlineSource | None:
    reminders = settings.config.get("reminders", {})
    if not reminders:
        return None
    config = dict(reminders["notion_tasks"])
    config.pop("create_default_status", None)
    for name in ("checkbox_properties", "excluded_status_values", "excluded_context_values"):
        config[name] = tuple(config.get(name, []))
    return NotionTaskDeadlineSource(notion, NotionTaskConfig(**config), settings.timezone)


def attach_applications(runtime: Runtime, notion: NotionClient) -> None:
    """Only full startup wires stateful applications (after check_only gate)."""
    settings = runtime.settings
    planning_client = _planning_groq(settings)
    if planning_client is not None:
        runtime.message_intent_interpreter = GroqMessageIntentInterpreter(planning_client)
    runtime.planning_app = _calendar(settings, planning_client)
    runtime.inbox_app = _inbox(settings, notion)
    runtime.task_capture_app = _task_capture(settings, notion)
    runtime.progress = _progress(runtime)


def _planning_groq(settings: Settings) -> GroqClient | None:
    if not settings.groq_planning_api_key:
        return None
    return GroqClient(
        settings.groq_planning_api_key,
        settings.groq_planning_model,
        timeout=15,
        max_output_tokens=350,
        api_keys=settings.groq_api_keys,
    )


def _calendar(
    settings: Settings, planning_client: GroqClient | None
) -> CalendarPlanningApplication | None:
    config = settings.config.get("calendar_planning", {})
    if not config:
        return None
    client = GoogleCalendarClient(
        settings.google_calendar_client_secret_file
        or settings.root / "client_secret_not_configured.json",
        settings.root / "state" / "google_calendar_token.json",
        config.get("calendar_id", "primary"),
    )
    interpreter = GroqPlanningInterpreter(planning_client) if planning_client else None
    return CalendarPlanningApplication(
        SQLitePlanningRepository(settings.root / "state" / "calendar_planning.sqlite3"),
        GoogleCalendarAdapter(client, settings.timezone),
        settings.timezone,
        day_start=time.fromisoformat(config.get("day_start", "08:00")),
        day_end=time.fromisoformat(config.get("day_end", "22:00")),
        buffer_minutes=config.get("buffer_minutes", 15),
        interpreter=interpreter,
        timezone_name=settings.timezone.key,
    )


def _inbox(settings: Settings, notion: NotionClient) -> InboxCaptureApplication | None:
    config = settings.config.get("inbox")
    if not config:
        return None
    return InboxCaptureApplication(
        SQLiteInboxCaptureStore(settings.root / "state" / "inbox.sqlite3"),
        NotionInboxWriter(notion, NotionInboxConfig(**config)),
    )


def _task_capture(settings: Settings, notion: NotionClient) -> TaskCaptureApplication | None:
    config = settings.config.get("reminders", {}).get("notion_tasks")
    if not config:
        return None
    return TaskCaptureApplication(
        SQLiteTaskCaptureRepository(settings.root / "state" / "task_capture.sqlite3"),
        NotionTaskWriter(
            notion,
            NotionTaskWriterConfig(
                data_source_id=config["source_id"],
                title_property=config.get("title_property", "Name"),
                due_property=config.get("due_property", "Due"),
                status_property=config.get("status_property", "List"),
                default_status=config.get("create_default_status", "Backlog 🐛"),
            ),
        ),
    )


def _progress(runtime: Runtime) -> ProgressReports | None:
    settings = runtime.settings
    if not settings.groq_api_key:
        return None
    ai = settings.config.get("reports", {}).get("ai", {})
    engine = GroqClient(
        settings.groq_api_key,
        settings.groq_model,
        api_keys=settings.groq_api_keys,
        timeout=ai.get("timeout_seconds", 180),
        max_output_tokens=ai.get("max_output_tokens", 4_500),
    )
    analyzer = GroqProgressAnalyzer(
        engine,
        max_rounds=ai.get("max_rounds", 3),
        max_batch_chars=ai.get("max_batch_chars", 10_000),
        max_batches=ai.get("max_batches", 24),
        max_requests=ai.get("max_requests", 48),
        max_input_chars=ai.get("max_input_chars", 160_000),
        max_history_records=ai.get("max_history_records", 80),
        reasoning={
            "week": ai.get("weekly_reasoning", "medium"),
            "month": ai.get("monthly_reasoning", "high"),
        },
        scope=settings.telegram_chat_id or "owner",
    )
    return ProgressReports(
        runtime.report_sources,
        analyzer,
        SQLiteProgressArchive(settings.root / "state" / "progress.sqlite3"),
        settings.telegram_chat_id or "owner",
        clock=runtime.now,
    )
