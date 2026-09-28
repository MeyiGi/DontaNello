"""Small state records used by operational safeguards."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AlertStatus:
    active: bool = False
    first_failed_at: datetime | None = None
    last_attempt_at: datetime | None = None
    notification_count: int = 0
    recovery_pending: bool = False
