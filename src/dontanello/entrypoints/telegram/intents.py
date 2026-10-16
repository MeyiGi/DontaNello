"""Turn a natural-language message into one application action."""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from dontanello.modules.calendar_planning import (
    CalendarPlanningApplication,
    PlanRequest,
    TimeSlot,
)
from dontanello.modules.inbox import InboxCaptureApplication
from dontanello.modules.message_intent import (
    MessageIntent,
    MessageIntentInterpreter,
    MessageIntentUnavailable,
)
from dontanello.modules.reminders import ReminderApplication, ReminderDraft
from dontanello.modules.task_capture import TaskCaptureApplication, TaskDraft

from .ui import _telegram_markup

logger = logging.getLogger(__name__)
Reply = tuple[str, dict[str, Any] | None]

NO_INTERPRETER = (
    "Не удалось определить действие: Groq-маршрутизация не настроена. Ничего не сохранил."
)
GROQ_UNAVAILABLE = (
    "Groq временно не смог разобрать сообщение. Ничего не записал — попробуй ещё раз через минуту."
)
ROUTING_FAILED = "Не удалось обработать сообщение. Ничего не записал — попробуй ещё раз."
UNCLEAR = (
    "Не уверен, что сделать с сообщением: сохранить в Inbox, создать задачу, "
    "предложить время в календаре или поставить напоминание. Ничего не записал."
)
UNKNOWN_ACTION = (
    "Могу сохранить идею в Inbox, создать задачу, предложить время в календаре "
    "или поставить напоминание. Уточни, что сделать. Ничего не записал."
)


@dataclass
class IntentHandler:
    interpreter: MessageIntentInterpreter | None = None
    inbox: InboxCaptureApplication | None = None
    task_capture: TaskCaptureApplication | None = None
    planning: CalendarPlanningApplication | None = None
    reminders: ReminderApplication | None = None

    def handle(self, update_id: int, text: str, now: datetime) -> Reply:
        if self.interpreter is None:
            return NO_INTERPRETER, None

        inbox_pending = bool(self.inbox and self.inbox.has_pending_prompt(now))
        try:
            intent = self.interpreter.interpret(text, now, inbox_prompt_pending=inbox_pending)
        except MessageIntentUnavailable:
            logger.warning("Telegram message routing is temporarily unavailable via Groq")
            return GROQ_UNAVAILABLE, None
        except Exception as error:
            logger.warning(
                "Telegram message routing failed unexpectedly (%s)", type(error).__name__
            )
            return ROUTING_FAILED, None

        accepted = intent if intent is not None and intent.confidence != "low" else None
        destination = accepted.destination if accepted is not None else None

        # Only the Inbox application owns persistent pending state.
        if inbox_pending and destination != "inbox" and self.inbox is not None:
            self.inbox.clear_pending_prompt()

        if accepted is None:
            return UNCLEAR, None

        actions = {
            "inbox": self._save_inbox,
            "task": self._save_task,
            "calendar": self._save_calendar,
            "reminder": self._save_reminder,
        }
        if destination is None:
            return UNKNOWN_ACTION, None
        action = actions.get(destination)
        if action is None:
            return UNKNOWN_ACTION, None
        return action(update_id, text, accepted, now)

    def _save_inbox(
        self, update_id: int, _text: str, intent: MessageIntent, now: datetime
    ) -> Reply:
        if self.inbox is None:
            return "Inbox пока не настроен. Ничего не записал.", None
        return self.inbox.save_title(update_id, intent.title, now), None

    def _save_task(self, update_id: int, text: str, intent: MessageIntent, _now: datetime) -> Reply:
        if self.task_capture is None:
            return "Создание задач пока не настроено. Ничего не записал.", None
        draft = TaskDraft(intent.title, intent.due_date)
        result = self.task_capture.handle_draft(update_id, text, draft)
        return result.text, _telegram_markup(result)

    def _save_calendar(
        self, update_id: int, _text: str, intent: MessageIntent, now: datetime
    ) -> Reply:
        if self.planning is None:
            return "Планирование в Google Calendar пока не настроено. Ничего не менял.", None
        calendar = intent.calendar
        if calendar is None:
            return "Не получилось разобрать запрос для календаря. Ничего не менял.", None

        slot = None
        if calendar.start_time is not None and calendar.end_time is not None:
            slot = TimeSlot(
                datetime.combine(calendar.day, calendar.start_time, now.tzinfo),
                datetime.combine(calendar.day, calendar.end_time, now.tzinfo),
            )

        draft = PlanRequest(calendar.day, intent.title, calendar.duration_minutes, slot)
        result = self.planning.handle_request(update_id, intent.normalized_text, draft, now)
        return result.text, _telegram_markup(result)

    def _save_reminder(
        self, update_id: int, _text: str, intent: MessageIntent, now: datetime
    ) -> Reply:
        if self.reminders is None:
            return "Напоминания пока не настроены. Ничего не записал.", None
        reminder = intent.reminder
        if reminder is None:
            return "Не получилось создать напоминание. Ничего не записал.", None

        draft = ReminderDraft(
            intent.title,
            reminder.day,
            reminder.clock,
            reminder.daypart,
            reminder.delay_minutes,
        )
        result = self.reminders.handle_draft(update_id, draft, now)
        return result or "Не получилось создать напоминание. Ничего не записал.", None
