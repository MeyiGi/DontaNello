"""Natural-language routing suggestions for private Telegram messages."""

from .models import MessageIntent
from .ports import MessageIntentInterpreter, MessageIntentUnavailable

__all__ = ["MessageIntent", "MessageIntentInterpreter", "MessageIntentUnavailable"]
