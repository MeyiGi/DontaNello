"""Atomic JSON persistence for per-job alert throttling state."""

import json
import os
import threading
from datetime import datetime
from pathlib import Path

from dontanello.modules.operations.models import AlertStatus


class JsonAlertState:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self.values = self._read()

    def _read(self) -> dict[str, AlertStatus]:
        if not self.path.exists():
            return {}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Invalid alert state")
        values = {}
        for job, item in raw.items():
            if not isinstance(job, str) or not isinstance(item, dict):
                raise ValueError("Invalid alert state")
            try:
                active = item["active"]
                count = item["notification_count"]
                recovery_pending = item.get("recovery_pending", False)
                first = item.get("first_failed_at")
                attempted = item.get("last_attempt_at")
                if (
                    type(active) is not bool
                    or type(count) is not int
                    or count < 0
                    or type(recovery_pending) is not bool
                ):
                    raise ValueError
                values[job] = AlertStatus(
                    active,
                    datetime.fromisoformat(first) if first is not None else None,
                    datetime.fromisoformat(attempted) if attempted is not None else None,
                    count,
                    recovery_pending,
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("Invalid alert state") from exc
        return values

    def get(self, job: str) -> AlertStatus:
        with self._lock:
            return self.values.get(job, AlertStatus())

    def set(self, job: str, status: AlertStatus) -> None:
        with self._lock:
            updated = {**self.values, job: status}
            payload = {
                name: {
                    "active": item.active,
                    "first_failed_at": item.first_failed_at.isoformat()
                    if item.first_failed_at
                    else None,
                    "last_attempt_at": item.last_attempt_at.isoformat()
                    if item.last_attempt_at
                    else None,
                    "notification_count": item.notification_count,
                    "recovery_pending": item.recovery_pending,
                }
                for name, item in updated.items()
            }
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = self.path.with_name(self.path.name + ".tmp")
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(payload, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(self.path)
            os.chmod(self.path, 0o600)
            self.values = updated
