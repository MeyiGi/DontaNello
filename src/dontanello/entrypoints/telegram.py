"""Private Telegram commands delegate to the same report use cases as schedules."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from dontanello.integrations.telegram.client import TelegramClient
from dontanello.modules.calendar_planning import CalendarPlanningApplication, PlannerResponse
from dontanello.modules.inbox import InboxCaptureApplication
from dontanello.modules.reminders import ReminderApplication
from dontanello.modules.reports import (
    DeliveryService,
    Period,
    previous_month,
    previous_week,
)
from dontanello.platform.telegram_cursor import TelegramCursor

PERSONAL_KEYBOARD = {
    "keyboard": [
        [{"text": "📈 Неделя"}, {"text": "📆 Месяц"}],
        [{"text": "📋 Мои задачи"}, {"text": "📥 Inbox"}],
        [{"text": "⏰ Напоминания"}, {"text": "⚙️ Настройки дедлайнов"}],
        [{"text": "ℹ️ Статус"}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
    "input_field_placeholder": "Напиши задачу, напоминание или идею…",
}

_KEYBOARD_COMMANDS = {
    "📈 Неделя": "/week",
    "📆 Месяц": "/month",
    "📋 Мои задачи": "/tasks",
    "📥 Inbox": "/inbox",
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
                reminder_request = bool(
                    self.reminders and self.reminders.accepts_message(routed_text)
                )
                inbox_request = bool(
                    self.inbox and self.inbox.accepts_message(routed_text, now, update_id)
                )
                planning_request = bool(
                    self.planning and self.planning.accepts_message(routed_text, now)
                )
                if (
                    command_name in ("/week", "/month", "/start", "/help", "/status")
                    or reminder_request
                    or inbox_request
                    or planning_request
                ):
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
                        elif reminder_request and self.reminders:
                            text = self.reminders.handle_message(update_id, routed_text, now) or ""
                        elif planning_request and self.planning:
                            response = self.planning.handle_message(update_id, routed_text, now)
                            text = response.text if response else "Не получилось разобрать запрос."
                            reply_markup = _telegram_markup(response)
                        elif inbox_request and self.inbox:
                            text = self.inbox.handle_message(update_id, routed_text, now) or ""
                        else:
                            text = (
                                "DONTANELLO\n\n"
                                "Можно нажимать кнопки внизу чата — команды вводить не обязательно.\n"
                                "📈 ПРОГРЕСС\n"
                                "Кнопки «Неделя» и «Месяц» — обзоры прогресса.\n\n"
                                "📥 INBOX\n"
                                "Нажми «Inbox» или напиши, что сохранить: «Запиши в инбокс: узнать про аффинный шифр».\n\n"
                                "✅ МОИ ЗАДАЧИ\n"
                                "Кнопка «Мои задачи» покажет просроченное, задачи на сегодня и ближайшие дедлайны.\n"
                                "Кнопка «Настройки дедлайнов» открывает расписание уведомлений.\n\n"
                                "⏰ НАПОМИНАНИЯ\n"
                                "Кнопка «Напоминания» покажет активные; напиши: «Напомни завтра вечером позвонить».\n\n"
                                "Кнопка «Статус» покажет состояние бота."
                            )
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

    def _handle_callback(self, callback: dict[str, Any]) -> None:
        callback_id = str(callback.get("id", ""))
        if callback_id:
            try:
                self.client.answer_callback_query(callback_id)
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
        if not authorized or self.planning is None or not callback_id:
            return
        now = self.now()
        key = f"calendar-callback:{callback_id}"
        if not self.delivery.needs_delivery(key, now):
            return
        text = self.delivery.existing_text(key)
        markup = self.delivery.existing_reply_markup(key)
        if text is None:
            data = callback.get("data")
            response = self.planning.handle_callback(str(data or ""), now)
            text = response.text
            markup = _telegram_markup(response)
        self.delivery.deliver(key, text, now, reply_markup=markup)
        if not self.delivery.is_terminal(key):
            raise RuntimeError("Queued calendar response awaiting delivery retry")


def _telegram_markup(response: PlannerResponse | None) -> dict[str, Any] | None:
    if response is None or not response.button_rows:
        return None
    return {
        "inline_keyboard": [
            [{"text": button.label, "callback_data": button.callback_data} for button in row]
            for row in response.button_rows
        ]
    }
