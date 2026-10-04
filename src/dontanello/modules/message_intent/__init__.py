"""Natural-language routing suggestions for private Telegram messages."""

from .models import MessageIntent
from .ports import MessageIntentInterpreter

__all__ = ["MessageIntent", "MessageIntentInterpreter"]
