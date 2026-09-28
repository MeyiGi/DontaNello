"""Operational workflows independent of Telegram and filesystem details."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .models import AlertStatus
from .ports import AlertSender, AlertState, BackupStore


@dataclass
class ErrorMonitor:
    state: AlertState
    sender: AlertSender
    cooldown_seconds: int = 3600

    def __post_init__(self) -> None:
        if self.cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be non-negative")

    @staticmethod
    def _job_label(job: str) -> str:
        # Job names come from internal wiring. Still constrain output in case
        # a caller supplies a dynamic value.
        label = "".join(
            character if character.isalnum() or character in "_.-" else "_" for character in job
        )[:64]
        return label or "задание"

    def failed(self, job: str, now: datetime) -> None:
        """Record a failure and send a bounded, generic alert when due."""
        try:
            status = self.state.get(job)
            label = self._job_label(job)
            if not status.active:
                updated = AlertStatus(True, now, now, status.notification_count + 1, False)
                message = f"Сбой автоматизации «{label}». Подробности доступны в журнале."
            elif status.last_attempt_at is None or now - status.last_attempt_at >= timedelta(
                seconds=self.cooldown_seconds
            ):
                updated = AlertStatus(
                    True,
                    status.first_failed_at or now,
                    now,
                    status.notification_count + 1,
                    False,
                )
                message = f"Автоматизация «{label}» всё ещё работает с ошибкой. Подробности доступны в журнале."
            else:
                return
            # Persist before calling the remote sender: an ambiguous send is
            # bounded by the cooldown across process restarts.
            self.state.set(job, updated)
        except Exception:
            return
        try:
            self.sender.send(message)
        except Exception:
            # A notification outage must not escape into the job worker.
            pass

    def recovered(self, job: str, now: datetime) -> None:
        """Close an incident and retry its recovery notice at most hourly."""
        try:
            status = self.state.get(job)
            if not status.active and not status.recovery_pending:
                return
            if (
                status.recovery_pending
                and status.last_attempt_at is not None
                and now - status.last_attempt_at < timedelta(seconds=self.cooldown_seconds)
            ):
                return
            self.state.set(
                job,
                AlertStatus(
                    False,
                    status.first_failed_at,
                    now,
                    status.notification_count,
                    True,
                ),
            )
        except Exception:
            return
        try:
            self.sender.send(f"Автоматизация «{self._job_label(job)}» восстановлена.")
        except Exception:
            pass
        else:
            try:
                self.state.set(
                    job,
                    AlertStatus(
                        False,
                        status.first_failed_at,
                        now,
                        status.notification_count,
                        False,
                    ),
                )
            except Exception:
                pass


@dataclass(frozen=True)
class BackupService:
    store: BackupStore
    retention: int = 14

    def __post_init__(self) -> None:
        if self.retention < 1:
            raise ValueError("retention must be at least one")

    def run(self, now: datetime) -> int:
        """Create one daily snapshot and return the number pruned."""
        day: date = now.date()
        if self.store.exists(day):
            self.store.prune(self.retention)
            return 0
        self.store.create(day)
        self.store.prune(self.retention)
        return 1
