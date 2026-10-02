"""Composition root assembling concrete implementations."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from queue import Empty, SimpleQueue

from dontanello.entrypoints.telegram import TelegramCommands
from dontanello.entrypoints.worker import Job
from dontanello.integrations.groq.client import GroqClient
from dontanello.integrations.notion.client import NotionClient
from dontanello.integrations.telegram.client import TelegramClient
from dontanello.modules.completion import CompletionTracker
from dontanello.modules.completion.adapters.json_state import JsonCheckboxState
from dontanello.modules.completion.adapters.notion import (
    NotionCompletionConfig,
    NotionCompletionSource,
)
from dontanello.modules.operations import BackupService, ErrorMonitor
from dontanello.modules.operations.adapters.filesystem_backups import FileBackupStore
from dontanello.modules.operations.adapters.json_alerts import JsonAlertState
from dontanello.modules.operations.adapters.serialized_monitor import SerializedMonitor
from dontanello.modules.operations.adapters.telegram import TelegramAlertSender
from dontanello.modules.reports import (
    DeliveryService,
    Period,
    ProgressReports,
    ScheduledReports,
    build_report,
)
from dontanello.modules.reports.adapters.groq_progress import GroqProgressAnalyzer
from dontanello.modules.reports.adapters.notion import NotionReportConfig, NotionReportSource
from dontanello.modules.reports.adapters.sqlite_delivery import SQLiteDeliveryStore
from dontanello.modules.reports.adapters.sqlite_progress import SQLiteProgressArchive
from dontanello.modules.reports.adapters.telegram import TelegramReportSender
from dontanello.platform.clock import LocalClock
from dontanello.platform.settings import Settings
from dontanello.platform.telegram_cursor import TelegramCursor


@dataclass
class Runtime:
    settings: Settings
    sources: list[NotionCompletionSource]
    report_sources: list[NotionReportSource] = field(default_factory=list)
    monitor: SerializedMonitor | None = None
    outcomes: SimpleQueue[tuple[str, bool, datetime]] = field(default_factory=SimpleQueue)
    progress: ProgressReports | None = None

    def now(self) -> datetime:
        return datetime.now(self.settings.timezone)

    def report(self, period: Period, full: bool = False) -> str:
        if full:
            return build_report(period, self.report_sources, compact=False)
        if self.progress is None:
            raise ValueError("GROQ_API_KEY не задан для отчётов")
        return self.progress.report(period)

    def check(self) -> None:
        for source in self.sources:
            source.validate()
            print("Notion: " + source.config.name + " OK")
        if self.settings.telegram_token:
            telegram = TelegramClient(self.settings.telegram_token)
            print("Telegram: @" + telegram.username())
            if self.settings.telegram_chat_id:
                print(
                    "Telegram chat: " + telegram.chat_type(self.settings.telegram_chat_id) + " OK"
                )
        else:
            print("Telegram: ожидается TELEGRAM_BOT_TOKEN в .env")
        for report_source in self.report_sources:
            schema = report_source.client.request("GET", "data_sources/" + report_source.config.id)
            for name, kind in (
                (report_source.config.date_property, "date"),
                (report_source.config.title_property, "title"),
            ):
                if schema["properties"].get(name, {}).get("type") != kind:
                    raise ValueError(f"Report {report_source.config.name}: invalid field {name}")
            print("Report source: " + report_source.config.name + " OK")
        if self.settings.groq_api_key:
            models = GroqClient(
                self.settings.groq_api_key,
                self.settings.groq_model,
                api_keys=self.settings.groq_api_keys,
            ).models()
            if self.settings.groq_model not in models:
                raise ValueError("GROQ_MODEL недоступна")
            print("Groq: " + self.settings.groq_model + " OK")

    def jobs(self) -> list[Job]:
        state = JsonCheckboxState(self.settings.root / "state" / "checkboxes.json")
        clock = LocalClock(self.settings.timezone)
        jobs = [
            Job(
                f"completion_{index}",
                CompletionTracker(source, state, clock).run,
                group="completion",
            )
            for index, source in enumerate(self.sources)
        ]
        operations = self.settings.config.get("operations", {})
        backups = BackupService(
            FileBackupStore(self.settings.root), operations.get("backup_retention", 14)
        )
        jobs.append(
            Job("backups", lambda: backups.run(self.now()), group="backups", interval_seconds=3600)
        )
        if not self.settings.telegram_token or not self.settings.telegram_chat_id:
            return jobs
        telegram = TelegramClient(self.settings.telegram_token)
        self.monitor = SerializedMonitor(
            ErrorMonitor(
                JsonAlertState(self.settings.root / "state" / "alerts.json"),
                TelegramAlertSender(telegram, self.settings.telegram_chat_id),
                operations.get("alert_cooldown_seconds", 3600),
            )
        )
        journal = SQLiteDeliveryStore(self.settings.root / "state" / "reports.sqlite3")
        delivery = DeliveryService(
            journal, TelegramReportSender(telegram), self.settings.telegram_chat_id
        )
        reports = self.settings.config.get("reports", {})
        hour, minute = reports.get("hour", 9), reports.get("minute", 0)
        scheduler = ScheduledReports(
            delivery, self.report, journal.active_since(self.now()), hour, minute
        )

        def status() -> str:
            counts = journal.status()
            schedule = f"{hour:02}:{minute:02} ({self.settings.timezone.key})"
            enabled = "включены" if reports.get("enabled", False) else "выключены"
            return (
                f"Dontanello работает.\nОтчёты {enabled}: понедельник и первое число, {schedule}."
                "\nРезервная копия состояния: ежедневно."
                f"\nЧастей отчётов доставлено: {counts['sent']}; ожидают: {counts['pending']};"
                f" требуют проверки: {counts['uncertain']}."
            )

        commands = TelegramCommands(
            telegram,
            TelegramCursor(self.settings.root / "state" / "telegram_cursor.json"),
            self.settings.telegram_chat_id,
            delivery,
            self.report,
            self.now,
            status,
            full_report=lambda period: self.report(period, full=True),
        )
        jobs.append(Job("telegram", commands.run, group="telegram", interval_seconds=5))
        if reports.get("enabled", False):
            jobs.append(
                Job(
                    "reports",
                    lambda: scheduler.run(self.now()),
                    group="reports",
                    interval_seconds=60,
                )
            )
        jobs.append(Job("alerts", self.flush_outcomes, group="alerts", interval_seconds=1))
        return jobs

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


def build_runtime(settings: Settings) -> Runtime:
    client = NotionClient(settings.notion_token)
    sources = [
        NotionCompletionSource(client, NotionCompletionConfig(**source))
        for source in settings.config["completion_sources"]
    ]
    report_sources = [
        NotionReportSource(client, NotionReportConfig(**source), settings.timezone)
        for source in settings.config.get("reports", {}).get("sources", [])
    ]
    runtime = Runtime(settings, sources, report_sources)
    if settings.groq_api_key:
        ai = settings.config.get("reports", {}).get("ai", {})
        engine = GroqClient(
            settings.groq_api_key,
            settings.groq_model,
            api_keys=settings.groq_api_keys,
            timeout=ai.get("timeout_seconds", 180),
            max_output_tokens=ai.get("max_output_tokens", 4_500),
        )
        runtime.progress = ProgressReports(
            report_sources,
            GroqProgressAnalyzer(
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
            ),
            SQLiteProgressArchive(settings.root / "state" / "progress.sqlite3"),
            settings.telegram_chat_id or "owner",
            clock=runtime.now,
        )
    return runtime


def verify_backup(root: Path, snapshot_name: str) -> None:
    FileBackupStore(root).verify(snapshot_name)


def restore_backup(root: Path, snapshot_name: str, restore_delivery_history: bool = False) -> None:
    FileBackupStore(root).restore(snapshot_name, restore_delivery_history)
