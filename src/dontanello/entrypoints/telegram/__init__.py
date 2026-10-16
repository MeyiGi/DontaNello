"""Telegram bot entrypoint and its public command interface.

Usage:
    from dontanello.entrypoints.telegram import TelegramCommands
"""

from .facade import TelegramCommands
from .ui import KEYBOARD_COMMANDS, PERSONAL_KEYBOARD, _message_id, _telegram_markup  # noqa: F401

__all__ = ["TelegramCommands", "PERSONAL_KEYBOARD"]
