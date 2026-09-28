"""Serialize concurrent job outcomes through one shared error monitor."""

import threading
from dataclasses import dataclass, field
from datetime import datetime

from dontanello.modules.operations.application import ErrorMonitor


@dataclass
class SerializedMonitor:
    inner: ErrorMonitor
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def failed(self, job: str, now: datetime) -> None:
        with self._lock:
            self.inner.failed(job, now)

    def recovered(self, job: str, now: datetime) -> None:
        with self._lock:
            self.inner.recovered(job, now)
