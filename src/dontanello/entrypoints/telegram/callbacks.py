"""Handle inline-button callbacks with authorization and durable delivery."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from dontanello.integrations.telegram.client import TelegramClient
from dontanello.modules.calendar_planning import CalendarPlanningApplication, PlannerResponse
from dontanello.modules.reports import DeliveryService
from dontanello.modules.task_capture import TaskCaptureApplication, TaskResponse

from .ui import _message_id, _telegram_markup

_PROGRESS = {
    "add": "⏳ Перепроверяю выбранное время в Google Calendar…",
    "more": "⏳ Обновляю свободные варианты…",
    "undo": "⏳ Проверяю созданное событие и отменяю его…",
}


@dataclass
class CallbackHandler:
    client: TelegramClient
    chat_id: str
    delivery: DeliveryService
    now: Callable[[], datetime]
    planning: CalendarPlanningApplication | None = None
    task_capture: TaskCaptureApplication | None = None

    def handle(self, callback: dict[str, Any]) -> None:
        callback_id = str(callback.get("id", ""))
        if callback_id:
            try:
                self.client.answer_callback_query(callback_id, "⏳ Обрабатываю…")
            except Exception:
                pass  # Best effort; the durable operation below remains idempotent.

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
        key = f"calendar-callback:{callback_id}"  # Keep existing delivery journal keys.
        if not self.delivery.needs_delivery(key, now):
            return

        text = self.delivery.existing_text(key)
        markup = self.delivery.existing_reply_markup(key)
        data = str(callback.get("data") or "")
        progress_id = _message_id(message)
        calendar_action = data.startswith("p:")

        if text is None:
            if calendar_action and progress_id is not None:
                action = data.split(":", 2)[-1]
                if progress_text := _PROGRESS.get(action):
                    self._edit_progress(progress_id, progress_text)

            def show_progress(progress_text: str) -> None:
                if progress_id is not None:
                    self._edit_progress(progress_id, progress_text)

            response: PlannerResponse | TaskResponse
            if data.startswith("t:") and self.task_capture:
                response = self.task_capture.handle_callback(data)
            elif self.planning:
                response = self.planning.handle_callback(
                    data, now, on_progress=show_progress if data.endswith(":add") else None
                )
            else:
                return
            text = response.text
            markup = _telegram_markup(response)

        self.delivery.deliver(key, text, now, reply_markup=markup)
        if not self.delivery.is_terminal(key):
            raise RuntimeError("Queued calendar response awaiting delivery retry")
        if calendar_action and progress_id is not None:
            try:
                self.client.delete_message(self.chat_id, progress_id)
            except Exception:
                pass  # The durable response has already been delivered.

    def _edit_progress(self, message_id: int, text: str) -> None:
        try:
            self.client.edit_message_text(
                self.chat_id, message_id, text, reply_markup={"inline_keyboard": []}
            )
        except Exception:
            pass  # Progress UI must not prevent the actual operation.
