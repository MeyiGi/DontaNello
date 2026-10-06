"""Private Telegram commands delegate to the same report use cases as schedules."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from dontanello.integrations.telegram.client import TelegramClient
from dontanello.modules.calendar_planning import CalendarPlanningApplication, PlannerResponse
from dontanello.modules.inbox import InboxCaptureApplication
from dontanello.modules.message_intent import (
    MessageIntent,
    MessageIntentInterpreter,
    MessageIntentUnavailable,
)
from dontanello.modules.reminders import ReminderApplication
from dontanello.modules.reports import (
    DeliveryService,
    Period,
    previous_month,
    previous_week,
)
from dontanello.modules.task_capture import TaskCaptureApplication, TaskDraft, TaskResponse
from dontanello.modules.weather import WeatherApplication, WeatherUnavailable
from dontanello.platform.telegram_cursor import TelegramCursor

_logger = logging.getLogger(__name__)

PERSONAL_KEYBOARD = {
    "keyboard": [
        [{"text": "📋 Мои задачи"}, {"text": "📥 Inbox"}],
        [{"text": "🌤 Погода сегодня"}, {"text": "📅 Свободное время"}],
        [{"text": "⏰ Напоминания"}],
        [{"text": "⚙️ Настройки дедлайнов"}, {"text": "ℹ️ Статус"}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
    "input_field_placeholder": "Напиши задачу, напоминание или идею…",
}

_KEYBOARD_COMMANDS = {
    "📋 Мои задачи": "/tasks",
    "📥 Inbox": "/inbox",
    "🌤 Погода сегодня": "/weather",
    "📅 Свободное время": "/availability",
    "⏰ Напоминания": "/reminders",
    "⚙️ Настройки дедлайнов": "/tasksettings",
    "ℹ️ Статус": "/status",
}


@dataclass
class TelegramCommands:
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
        offset = self.cursor.load()
        handled = 0
        for update in self.client.updates(offset):
            update_id = int(update["update_id"])
            callback = update.get("callback_query")
            if isinstance(callback, dict):
                self._handle_callback(callback)
                self.cursor.save(update_id + 1)
                handled += 1
                continue
            message = update.get("message", {})
            chat = message.get("chat", {})
            # Check identity/type before reading Notion or constructing reports.
            authorized = (
                str(chat.get("id", "")) == self.chat_id
                and chat.get("type") == "private"
                and not message.get("from", {}).get("is_bot", False)
            )
            if authorized:
                now = self.now()
                raw_text = str(message.get("text") or "")
                routed_text = _KEYBOARD_COMMANDS.get(raw_text, raw_text)
                command = routed_text.split(maxsplit=1)
                command_name = command[0].split("@", 1)[0].lower() if command else ""
                availability_request = command_name == "/availability"
                weather_request = command_name == "/weather" or routed_text.strip().casefold() in {
                    "погода",
                    "погода сегодня",
                    "какая погода",
                    "какая погода сегодня",
                }
                natural_text = bool(routed_text.strip()) and not routed_text.lstrip().startswith(
                    "/"
                )
                reminder_request = bool(
                    self.reminders and self.reminders.accepts_message(routed_text)
                )
                inbox_command = bool(self.inbox and command_name == "/inbox")
                planning_followup = bool(
                    self.planning and self.planning.accepts_pending_reply(routed_text, now)
                )
                supported_command = (
                    command_name
                    in {
                        "/week",
                        "/month",
                        "/start",
                        "/help",
                        "/status",
                        "/availability",
                    }
                    or weather_request
                    or reminder_request
                    or inbox_command
                )
                if supported_command or planning_followup or natural_text:
                    key = f"command:{update_id}"
                    if self.delivery.needs_delivery(key, now):
                        existing = self.delivery.existing_text(key)
                        reply_markup = self.delivery.existing_reply_markup(key)
                        if existing is not None:
                            text = existing
                        elif command_name in ("/week", "/month"):
                            period = (
                                previous_week(now.date(), self.weekly_weekday)
                                if command_name == "/week"
                                else previous_month(now.date())
                            )
                            full = len(command) > 1 and command[1].strip().lower() == "full"
                            text = (
                                self.full_report(period)
                                if full and self.full_report
                                else self.build(period)
                            )
                        elif command_name == "/status":
                            text = self.status()
                        elif availability_request:
                            availability_response = (
                                self.planning.show_availability(now.date(), now)
                                if self.planning
                                else PlannerResponse("Просмотр календаря пока не настроен.")
                            )
                            text = availability_response.text
                            reply_markup = _telegram_markup(availability_response)
                        elif weather_request:
                            if self.weather is None:
                                text = "Погода пока не настроена."
                            else:
                                try:
                                    text = self.weather.today(now)
                                except WeatherUnavailable:
                                    _logger.warning("Weather forecast is temporarily unavailable")
                                    text = "Не получилось загрузить прогноз. Попробуй ещё раз чуть позже."
                        elif reminder_request and self.reminders:
                            if natural_text and self.inbox and self.inbox.has_pending_prompt(now):
                                self.inbox.clear_pending_prompt()
                            text = self.reminders.handle_message(update_id, routed_text, now) or ""
                        elif inbox_command and self.inbox:
                            text = self.inbox.handle_message(update_id, routed_text, now) or ""
                        elif planning_followup and self.planning:
                            planner_response = self.planning.handle_message(
                                update_id, routed_text, now
                            )
                            text = (
                                planner_response.text
                                if planner_response
                                else "Не получилось разобрать запрос."
                            )
                            reply_markup = _telegram_markup(planner_response)
                        elif natural_text:
                            text, reply_markup = self._route_natural_message(
                                update_id, routed_text, now
                            )
                        else:
                            text = (
                                "DONTANELLO\n\n"
                                "Можно нажимать кнопки внизу чата — команды вводить не обязательно.\n"
                                "📅 СВОБОДНОЕ ВРЕМЯ\n"
                                "Кнопка покажет промежутки, свободные по Google Calendar; дни можно переключать.\n"
                                "🌤 ПОГОДА СЕГОДНЯ\n"
                                "Покажет прогноз на сегодня; краткая сводка также приходит каждое утро.\n\n"
                                "Можно написать: «Хочу позаниматься безопасностью 1,5 часа» — если день не указан, предложу время на сегодня.\n\n"
                                "📥 INBOX\n"
                                "Нажми «Inbox» или напиши, что сохранить: «Запиши в инбокс: узнать про аффинный шифр».\n\n"
                                "✅ МОИ ЗАДАЧИ\n"
                                "Кнопка «Мои задачи» покажет просроченное, задачи на сегодня и ближайшие дедлайны.\n"
                                "Чтобы добавить задачу в Notion, напиши: «Добавь задачу прочитать презентацию до пятницы» — я сразу сохраню её.\n"
                                "Кнопка «Настройки дедлайнов» открывает расписание уведомлений.\n\n"
                                "⏰ НАПОМИНАНИЯ\n"
                                "Кнопка «Напоминания» покажет активные; напиши: «Напомни завтра вечером позвонить».\n\n"
                                "Кнопка «Статус» покажет состояние бота."
                            )
                        if reply_markup is None:
                            # Sending the latest persistent keyboard also replaces stale buttons
                            # left by older bot versions in the Telegram client.
                            reply_markup = PERSONAL_KEYBOARD
                        self.delivery.deliver(
                            key,
                            text,
                            now,
                            parse_mode="HTML" if command_name == "/tasks" else None,
                            reply_markup=reply_markup,
                        )
                    if not self.delivery.is_terminal(key):
                        # Safe rejection backoff: keep this update queued until due.
                        raise RuntimeError("Queued report awaiting delivery retry")
                    handled += 1
            # Failed report construction/rejected sends retain the update for retry.
            # Uncertain sends are journaled and skipped on replay, preventing blind duplicates.
            self.cursor.save(update_id + 1)
        return handled

    def _route_natural_message(
        self, update_id: int, text: str, now: datetime
    ) -> tuple[str, dict[str, Any] | None]:
        if self.message_intent is None:
            return (
                "Не удалось определить действие: Groq-маршрутизация не настроена. Ничего не сохранил.",
                None,
            )

        inbox_pending = bool(self.inbox and self.inbox.has_pending_prompt(now))
        try:
            intent = self.message_intent.interpret(
                text,
                now,
                inbox_prompt_pending=inbox_pending,
            )
        except MessageIntentUnavailable:
            _logger.warning("Telegram message routing is temporarily unavailable via Groq")
            return (
                "Groq временно не смог разобрать сообщение. Ничего не записал — "
                "попробуй ещё раз через минуту.",
                None,
            )
        except Exception as error:
            _logger.warning(
                "Telegram message routing failed unexpectedly (%s)", type(error).__name__
            )
            return (
                "Не удалось обработать сообщение. Ничего не записал — попробуй ещё раз.",
                None,
            )

        if intent is None or intent.confidence == "low":
            if inbox_pending and self.inbox:
                self.inbox.clear_pending_prompt()
            return (
                "Не уверен, что сделать с сообщением: сохранить в Inbox, создать задачу, "
                "предложить время в календаре или поставить напоминание. Ничего не записал.",
                None,
            )

        handlers: dict[str, Callable[[MessageIntent], tuple[str, dict[str, Any] | None]]] = {
            "inbox": lambda value: self._save_inbox_intent(update_id, value, now),
            "task": lambda value: self._save_task_intent(update_id, text, value, now),
            "calendar": lambda value: self._save_calendar_intent(update_id, value, now),
            "reminder": lambda value: self._save_reminder_intent(update_id, value, now),
        }
        handler = handlers.get(intent.destination)
        if handler is None:
            if inbox_pending and self.inbox:
                self.inbox.clear_pending_prompt()
            return (
                "Могу сохранить идею в Inbox, создать задачу, предложить время в календаре "
                "или поставить напоминание. Уточни, что сделать. Ничего не записал.",
                None,
            )

        if intent.destination != "inbox" and inbox_pending and self.inbox:
            self.inbox.clear_pending_prompt()
        return handler(intent)

    def _save_inbox_intent(
        self, update_id: int, intent: MessageIntent, now: datetime
    ) -> tuple[str, None]:
        if self.inbox is None:
            return "Inbox пока не настроен. Ничего не записал.", None
        return self.inbox.save_title(update_id, intent.title, now), None

    def _save_task_intent(
        self, update_id: int, request_text: str, intent: MessageIntent, now: datetime
    ) -> tuple[str, dict[str, Any] | None]:
        if self.task_capture is None:
            return "Создание задач пока не настроено. Ничего не записал.", None
        response = self.task_capture.handle_draft(
            update_id,
            request_text,
            TaskDraft(intent.title, intent.due_date),
        )
        return response.text, _telegram_markup(response)

    def _save_calendar_intent(
        self, update_id: int, intent: MessageIntent, now: datetime
    ) -> tuple[str, dict[str, Any] | None]:
        if self.planning is None:
            return "Планирование в Google Calendar пока не настроено. Ничего не менял.", None
        response = self.planning.handle_message(update_id, intent.normalized_text, now)
        if response is None:
            return "Не получилось разобрать запрос для календаря. Ничего не менял.", None
        return response.text, _telegram_markup(response)

    def _save_reminder_intent(
        self, update_id: int, intent: MessageIntent, now: datetime
    ) -> tuple[str, None]:
        if self.reminders is None:
            return "Напоминания пока не настроены. Ничего не записал.", None
        text = self.reminders.handle_message(update_id, intent.normalized_text, now)
        return text or "Не получилось создать напоминание. Ничего не записал.", None

    def _handle_callback(self, callback: dict[str, Any]) -> None:
        callback_id = str(callback.get("id", ""))
        if callback_id:
            try:
                self.client.answer_callback_query(callback_id, "⏳ Обрабатываю…")
            except Exception:
                # Acknowledgement is best effort; the durable action below is idempotent.
                pass
        message = callback.get("message")
        chat = message.get("chat", {}) if isinstance(message, dict) else {}
        sender = callback.get("from", {})
        authorized = (
            str(chat.get("id", "")) == self.chat_id
            and chat.get("type") == "private"
            and str(sender.get("id", "")) == self.chat_id
            and not sender.get("is_bot", False)
        )
        if (
            not authorized
            or not callback_id
            or (self.planning is None and self.task_capture is None)
        ):
            return
        now = self.now()
        key = f"calendar-callback:{callback_id}"
        if not self.delivery.needs_delivery(key, now):
            return
        text = self.delivery.existing_text(key)
        markup = self.delivery.existing_reply_markup(key)
        data = str(callback.get("data") or "")
        progress_message_id = _message_id(message)
        is_calendar_action = data.startswith("p:")
        if text is None and is_calendar_action and progress_message_id is not None:
            action = data.split(":", 2)[-1]
            progress_text = {
                "add": "⏳ Перепроверяю выбранное время в Google Calendar…",
                "more": "⏳ Обновляю свободные варианты…",
                "undo": "⏳ Проверяю созданное событие и отменяю его…",
            }.get(action)
            if progress_text:
                self._edit_callback_message(progress_message_id, progress_text)

        def show_progress(progress_text: str) -> None:
            if progress_message_id is not None:
                self._edit_callback_message(progress_message_id, progress_text)

        if text is None:
            response: PlannerResponse | TaskResponse
            if data.startswith("t:") and self.task_capture:
                response = self.task_capture.handle_callback(data)
            elif self.planning:
                response = self.planning.handle_callback(
                    data,
                    now,
                    on_progress=show_progress if data.endswith(":add") else None,
                )
            else:
                return
            text = response.text
            markup = _telegram_markup(response)
        self.delivery.deliver(key, text, now, reply_markup=markup)
        if not self.delivery.is_terminal(key):
            raise RuntimeError("Queued calendar response awaiting delivery retry")
        if is_calendar_action and progress_message_id is not None:
            try:
                self.client.delete_message(self.chat_id, progress_message_id)
            except Exception:
                # The durable response was delivered; an obsolete progress card is harmless.
                pass

    def _edit_callback_message(self, message_id: int, text: str) -> None:
        try:
            self.client.edit_message_text(
                self.chat_id,
                message_id,
                text,
                reply_markup={"inline_keyboard": []},
            )
        except Exception:
            # Progress UI must never block an authorized calendar operation.
            pass


def _message_id(message: object) -> int | None:
    if not isinstance(message, dict):
        return None
    value = message.get("message_id")
    return value if isinstance(value, int) and value > 0 else None


def _telegram_markup(response: PlannerResponse | TaskResponse | None) -> dict[str, Any] | None:
    if response is None or not response.button_rows:
        return None
    return {
        "inline_keyboard": [
            [{"text": button.label, "callback_data": button.callback_data} for button in row]
            for row in response.button_rows
        ]
    }
