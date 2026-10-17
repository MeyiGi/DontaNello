"""Configurable weekday learning plans with durable morning Telegram delivery."""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, tzinfo

from .models import ReminderSendRejected, ReminderSendUncertain
from .ports import ReminderSender, RoutineDeliveryStore

_LOGGER = logging.getLogger(__name__)
_RETRY_DELAY = timedelta(minutes=5)
_WEEKDAYS = (
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
)


def render_weekly_routine(day: date, days: tuple[tuple[str, ...], ...]) -> str:
    """Select only today's activities; the configuration starts on Monday."""
    activities = days[day.weekday()]
    lines = [
        f"🌅 План развития · {_WEEKDAYS[day.weekday()]}, {day:%d.%m.%Y}",
        "",
        *(f"• {activity}" for activity in activities),
        "",
        "💚 Это ориентир, а не обязанность. При усталости можно отдохнуть.",
    ]
    return "\n".join(lines)


class WeeklyRoutine:
    """One morning delivery per Bishkek-local date, without blind retries."""

    def __init__(
        self,
        store: RoutineDeliveryStore,
        sender: ReminderSender,
        chat_id: str,
        timezone: tzinfo,
        send_time: time,
        days: tuple[tuple[str, ...], ...],
    ) -> None:
        if not chat_id.strip() or len(days) != 7 or any(not day for day in days):
            raise ValueError("A private chat and seven non-empty weekday plans are required")
        if send_time.hour >= 12 or send_time.second or send_time.microsecond:
            raise ValueError("Routine send time must be in the morning, to whole minutes")
        self.store = store
        self.sender = sender
        self.chat_id = chat_id
        self.timezone = timezone
        self.send_time = send_time
        self.days = days
        self.store.recover_routine_inflight()

    def run(self, now: datetime) -> int:
        local_now = now.astimezone(self.timezone)
        local_time = local_now.time().replace(tzinfo=None)
        # Delayed startup may send later in the morning, but never after noon.
        if local_time < self.send_time or local_time >= time(12):
            return 0

        today = local_now.date()
        state = self.store.routine_state(today)
        if state is None:
            self.store.prepare_routine(today, render_weekly_routine(today, self.days))
            state = self.store.routine_state(today)
        if state is None or state[0] != "pending":
            return 0
        if state[2] is not None and state[2] > now:
            return 0
        if not self.store.claim_routine(today, now):
            return 0

        try:
            self.sender.send(self.chat_id, state[1])
        except ReminderSendRejected:
            self.store.finish_routine(today, "pending", now + _RETRY_DELAY)
            _LOGGER.warning("Weekly routine rejected; will retry in five minutes")
            return 0
        except (ReminderSendUncertain, Exception):
            self.store.finish_routine(today, "uncertain")
            _LOGGER.warning("Weekly routine delivery uncertain; automatic resend disabled")
            return 0

        self.store.finish_routine(today, "sent")
        return 1
