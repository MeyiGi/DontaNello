"""Validate Telegram messages, choose a use case, and deliver its reply."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from dontanello.modules.calendar_planning import CalendarPlanningApplication, PlannerResponse
from dontanello.modules.inbox import InboxCaptureApplication
from dontanello.modules.reminders import ReminderApplication
from dontanello.modules.reports import DeliveryService, Period, previous_month, previous_week
from dontanello.modules.weather import WeatherApplication, WeatherUnavailable

from .intents import IntentHandler, Reply
from .ui import HELP_TEXT, KEYBOARD_COMMANDS, PERSONAL_KEYBOARD, _telegram_markup

_logger = logging.getLogger(__name__)
_WEATHER_ALIASES = {"погода", "погода сегодня", "какая погода", "какая погода сегодня"}
_BUILTIN_COMMANDS = {"/week", "/month", "/start", "/help", "/status", "/availability"}


@dataclass(frozen=True)
class Request:
    text: str
    name: str
    argument: str
    natural: bool
    weather: bool
    reminder: bool
    inbox: bool
    planning_followup: bool


@dataclass
class MessageHandler:
    chat_id: str
    delivery: DeliveryService
    now: Callable[[], datetime]
    build: Callable[[Period], str]
    status: Callable[[], str]
    intents: IntentHandler
    full_report: Callable[[Period], str] | None = None
    weekly_weekday: int = 0
    reminders: ReminderApplication | None = None
    inbox: InboxCaptureApplication | None = None
    planning: CalendarPlanningApplication | None = None
    weather: WeatherApplication | None = None

    def handle(self, update_id: int, message: dict[str, Any]) -> bool:
        chat = message.get("chat", {})
        if not (
            str(chat.get("id", "")) == self.chat_id
            and chat.get("type") == "private"
            and not message.get("from", {}).get("is_bot", False)
        ):
            return False

        now = self.now()
        request = self._parse(str(message.get("text") or ""), now)
        if request is None:
            return False

        key = f"command:{update_id}"
        if self.delivery.needs_delivery(key, now):
            text = self.delivery.existing_text(key)
            markup = self.delivery.existing_reply_markup(key)
            if text is None:
                text, markup = self._reply(request, update_id, now)
            self.delivery.deliver(
                key,
                text,
                now,
                parse_mode="HTML" if request.name == "/tasks" else None,
                reply_markup=markup if markup is not None else PERSONAL_KEYBOARD,
            )

        # Keep the offset unchanged while a delivery is pending/rejected.
        if not self.delivery.is_terminal(key):
            raise RuntimeError("Queued report awaiting delivery retry")
        return True

    def _parse(self, raw: str, now: datetime) -> Request | None:
        text = KEYBOARD_COMMANDS.get(raw, raw)
        parts = text.split(maxsplit=1)
        name = parts[0].split("@", 1)[0].lower() if parts else ""
        natural = bool(text.strip()) and not text.lstrip().startswith("/")
        weather = name == "/weather" or text.strip().casefold() in _WEATHER_ALIASES
        reminder = bool(self.reminders and self.reminders.accepts_message(text))
        inbox = bool(self.inbox and name == "/inbox")
        planning_followup = bool(self.planning and self.planning.accepts_pending_reply(text, now))
        if not (
            name in _BUILTIN_COMMANDS
            or weather
            or reminder
            or inbox
            or planning_followup
            or natural
        ):
            return None
        return Request(
            text,
            name,
            parts[1] if len(parts) > 1 else "",
            natural,
            weather,
            reminder,
            inbox,
            planning_followup,
        )

    def _reply(self, request: Request, update_id: int, now: datetime) -> Reply:
        if request.name in {"/week", "/month"}:
            period = (
                previous_week(now.date(), self.weekly_weekday)
                if request.name == "/week"
                else previous_month(now.date())
            )
            full = request.argument.strip().lower() == "full"
            return (
                self.full_report(period) if full and self.full_report else self.build(period)
            ), None
        if request.name == "/status":
            return self.status(), None
        if request.name == "/availability":
            response = (
                self.planning.show_availability(now.date(), now)
                if self.planning
                else PlannerResponse("Просмотр календаря пока не настроен.")
            )
            return response.text, _telegram_markup(response)
        if request.weather:
            return self._weather(now), None
        if request.reminder and self.reminders:
            if request.natural and self.inbox:
                if self.inbox.has_pending_prompt(now):
                    self.inbox.clear_pending_prompt()
            return self.reminders.handle_message(update_id, request.text, now) or "", None
        if request.inbox and self.inbox:
            return self.inbox.handle_message(update_id, request.text, now) or "", None
        if request.planning_followup and self.planning:
            followup = self.planning.handle_message(update_id, request.text, now)
            return (
                followup.text if followup else "Не получилось разобрать запрос.",
                _telegram_markup(followup),
            )
        if request.natural:
            return self.intents.handle(update_id, request.text, now)
        return HELP_TEXT, None

    def _weather(self, now: datetime) -> str:
        if self.weather is None:
            return "Погода пока не настроена."
        try:
            return self.weather.today(now)
        except WeatherUnavailable:
            _logger.warning("Weather forecast is temporarily unavailable")
            return "Не получилось загрузить прогноз. Попробуй ещё раз чуть позже."
