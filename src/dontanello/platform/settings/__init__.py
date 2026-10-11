"""Public entry point for settings imports.

Consumers can continue using `from dontanello.platform.settings import Settings, load_settings`.
"""

from .loader import load_settings
from .schema import (
    CalendarPlanning,
    CompletionSource,
    Configuration,
    Inbox,
    NotionTasks,
    Operations,
    Reminders,
    ReportAI,
    Reports,
    ReportSource,
    Settings,
    Weather,
)

__all__ = [
    "CalendarPlanning",
    "CompletionSource",
    "Configuration",
    "Inbox",
    "NotionTasks",
    "Operations",
    "Reminders",
    "ReportAI",
    "ReportSource",
    "Reports",
    "Settings",
    "Weather",
    "load_settings",
]
