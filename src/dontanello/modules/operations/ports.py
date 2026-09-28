"""Ports owned by the operations capability."""

from datetime import date
from typing import Protocol

from .models import AlertStatus


class AlertState(Protocol):
    def get(self, job: str) -> AlertStatus: ...
    def set(self, job: str, status: AlertStatus) -> None: ...


class AlertSender(Protocol):
    def send(self, text: str) -> None: ...


class BackupStore(Protocol):
    def exists(self, day: date) -> bool: ...
    def create(self, day: date) -> None: ...
    def prune(self, keep: int) -> None: ...
