"""Telegram polling facade that delegates each update to its focused handler."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from dontanello.integrations.telegram.client import TelegramClient
from dontanello.modules.calendar_planning import CalendarPlanningApplication
from dontanello.modules.inbox import InboxCaptureApplication
from dontanello.modules.message_intent import MessageIntentInterpreter
from dontanello.modules.reminders import ReminderApplication
from dontanello.modules.reports import DeliveryService, Period
from dontanello.modules.task_capture import TaskCaptureApplication
from dontanello.modules.weather import WeatherApplication
from dontanello.platform.telegram_cursor import TelegramCursor

from .callbacks import CallbackHandler
from .intents import IntentHandler
from .messages import MessageHandler


@dataclass
class TelegramCommands:
    """Preserve the command interface while delegating to focused handlers."""

    client: TelegramClient
    cursor: TelegramCursor
    chat_id: str
    delivery: DeliveryService
    build: Callable[[Period], str]
    now: Callable[[], datetime]
    status: Callable[[], str]
    full_report: Callable[[Period], str] | None = None
    weekly_weekday: int = 0
    reminders: ReminderApplication | None = None
    inbox: InboxCaptureApplication | None = None
    planning: CalendarPlanningApplication | None = None
    task_capture: TaskCaptureApplication | None = None
    message_intent: MessageIntentInterpreter | None = None
    weather: WeatherApplication | None = None

    def run(self) -> int:
        intents = IntentHandler(
            interpreter=self.message_intent,
            inbox=self.inbox,
            task_capture=self.task_capture,
            planning=self.planning,
            reminders=self.reminders,
        )
        messages = MessageHandler(
            chat_id=self.chat_id,
            delivery=self.delivery,
            now=self.now,
            build=self.build,
            status=self.status,
            full_report=self.full_report,
            weekly_weekday=self.weekly_weekday,
            reminders=self.reminders,
            inbox=self.inbox,
            planning=self.planning,
            weather=self.weather,
            intents=intents,
        )
        callbacks = CallbackHandler(
            client=self.client,
            chat_id=self.chat_id,
            delivery=self.delivery,
            now=self.now,
            planning=self.planning,
            task_capture=self.task_capture,
        )

        handled = 0
        for update in self.client.updates(self.cursor.load()):
            update_id = int(update["update_id"])
            callback = update.get("callback_query")

            if isinstance(callback, dict):
                callbacks.handle(callback)
                handled += 1
            elif messages.handle(update_id, update.get("message", {})):
                handled += 1

            # Advance only after durable delivery (or an intentionally skipped update).
            self.cursor.save(update_id + 1)

        return handled
