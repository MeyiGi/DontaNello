"""Validated intent suggestions for private Telegram messages."""

from dataclasses import dataclass
from datetime import date
from typing import Literal

IntentDestination = Literal["inbox", "task", "calendar", "reminder", "clarify", "other"]
IntentConfidence = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class MessageIntent:
    destination: IntentDestination
    confidence: IntentConfidence
    title: str = ""
    due_date: date | None = None
    normalized_text: str = ""
