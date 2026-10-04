"""Model boundary for interpreting a private Telegram message."""

from datetime import datetime
from typing import Protocol

from .models import MessageIntent


class MessageIntentInterpreter(Protocol):
    def interpret(
        self,
        text: str,
        now: datetime,
        *,
        inbox_prompt_pending: bool,
    ) -> MessageIntent | None: ...
