"""Assemble DontaNello services and expose its startup API.

Detailed wiring lives alongside this file in services.py, jobs.py and checks.py.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from queue import Empty, SimpleQueue

from dontanello.entrypoints.worker import Job
from dontanello.integrations.notion.client import NotionClient
from dontanello.modules.calendar_planning import CalendarPlanningApplication
from dontanello.modules.completion.adapters.notion import NotionCompletionSource
from dontanello.modules.inbox import InboxCaptureApplication
from dontanello.modules.message_intent import MessageIntentInterpreter
from dontanello.modules.operations.adapters.filesystem_backups import FileBackupStore
from dontanello.modules.operations.adapters.serialized_monitor import SerializedMonitor
from dontanello.modules.reminders import ReminderApplication
from dontanello.modules.reminders.adapters.notion import NotionTaskDeadlineSource
from dontanello.modules.reports import Period, ProgressReports, build_report
from dontanello.modules.reports.adapters.notion import NotionReportSource
from dontanello.modules.task_capture import TaskCaptureApplication
from dontanello.modules.weather import WeatherApplication
from dontanello.platform.settings import Settings

from .checks import check_connections
from .jobs import register_jobs
from .services import (
    attach_applications,
    completion_sources,
    deadline_source,
    report_sources,
)


@dataclass
class Runtime:
    """The running application's services and their lifecycle hooks."""

    settings: Settings
    sources: list[NotionCompletionSource]
    report_sources: list[NotionReportSource] = field(default_factory=list)
    monitor: SerializedMonitor | None = None
    outcomes: SimpleQueue[tuple[str, bool, datetime]] = field(default_factory=SimpleQueue)
    progress: ProgressReports | None = None
    task_deadline_source: NotionTaskDeadlineSource | None = None
    reminders_app: ReminderApplication | None = None
    inbox_app: InboxCaptureApplication | None = None
    planning_app: CalendarPlanningApplication | None = None
    task_capture_app: TaskCaptureApplication | None = None
    message_intent_interpreter: MessageIntentInterpreter | None = None
    weather_app: WeatherApplication | None = None

    def now(self) -> datetime:
        return datetime.now(self.settings.timezone)

    def report(self, period: Period, full: bool = False) -> str:
        if full:
            return build_report(period, self.report_sources, compact=False)
        if self.progress is None:
            raise ValueError("GROQ_API_KEY не задан для отчётов")
        return self.progress.report(period)

    def check(self) -> None:
        """Check remote connections without initializing writable local state."""
        check_connections(self)

    def jobs(self) -> list[Job]:
        """Recover interrupted writes and register jobs. Caller holds worker_lock."""
        if self.inbox_app is not None:
            self.inbox_app.recover_inflight()
        if self.task_capture_app is not None:
            self.task_capture_app.recover_inflight()
        return register_jobs(self)

    def flush_outcomes(self) -> int:
        if not self.monitor:
            return 0
        for _ in range(100):
            try:
                job, failed, when = self.outcomes.get_nowait()
            except Empty:
                break
            if failed:
                self.monitor.failed(job, when)
            else:
                self.monitor.recovered(job, when)
        return 0

    def failed(self, job: str) -> None:
        if self.monitor and job != "alerts":
            self.outcomes.put((job, True, self.now()))

    def recovered(self, job: str) -> None:
        if self.monitor and job != "alerts":
            self.outcomes.put((job, False, self.now()))


def build_runtime(settings: Settings, *, check_only: bool = False) -> Runtime:
    """Compose read-only sources first; only full startup creates local state."""
    notion = NotionClient(settings.notion_token)
    runtime = Runtime(
        settings=settings,
        sources=completion_sources(settings, notion),
        report_sources=report_sources(settings, notion),
        task_deadline_source=deadline_source(settings, notion),
    )
    if not check_only:
        attach_applications(runtime, notion)
    return runtime


def verify_backup(root: Path, snapshot_name: str) -> None:
    FileBackupStore(root).verify(snapshot_name)


def restore_backup(root: Path, snapshot_name: str, restore_delivery_history: bool = False) -> None:
    FileBackupStore(root).restore(snapshot_name, restore_delivery_history)
