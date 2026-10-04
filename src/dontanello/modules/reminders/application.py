"""Task deadline digests and durable personal reminder use cases."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from html import escape

from .models import (
    ParsedReminder,
    ReminderParseError,
    ReminderSendRejected,
    ReminderSendUncertain,
    TaskDeadline,
    TaskDigestSettings,
)
from .parser import is_reminder_request, parse_reminder
from .ports import ReminderRepository, ReminderSender, TaskDeadlineSource

_WEEKDAY_NAMES = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
_WEEKDAY_INPUT = {
    "пн": 0,
    "вт": 1,
    "ср": 2,
    "чт": 3,
    "пт": 4,
    "сб": 5,
    "вс": 6,
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}
_DIGEST_HELP = (
    "Настройки дайджеста задач:\n"
    "/tasksettings on или /tasksettings off\n"
    "/tasksettings schedule daily 06:00\n"
    "/tasksettings schedule days пн,ср,пт 06:00\n"
    "/tasksettings horizon 7 — предупреждать за N дней; 0 — только сегодня и просроченные"
)


def select_due_tasks(
    tasks: tuple[TaskDeadline, ...], today: date, days_ahead: int
) -> tuple[TaskDeadline, ...]:
    if not 0 <= days_ahead <= 365:
        raise ValueError("days_ahead must be between 0 and 365")
    return tuple(
        sorted(
            (
                task
                for task in tasks
                if not task.completed
                and not task.cancelled
                and not task.excluded_from_digest
                and task.due_date <= today + timedelta(days=days_ahead)
            ),
            key=lambda task: (task.due_date, task.title.casefold(), task.id),
        )
    )


def render_task_digest(tasks: tuple[TaskDeadline, ...], today: date, days_ahead: int) -> str:
    selected = select_due_tasks(tasks, today, days_ahead)
    if not selected:
        return ""
    overdue = [task for task in selected if task.due_date < today]
    due_today = [task for task in selected if task.due_date == today]
    upcoming = [task for task in selected if task.due_date > today]
    lines = [f"🔔 <b>Дедлайны · {today:%d.%m.%Y}</b>"]
    if overdue:
        lines.extend(("", f"🔴 <b>Просрочено · {len(overdue)}</b>"))
        for task in overdue:
            late = (today - task.due_date).days
            lines.append(f"• {_task_link(task)} — на {late} {_day_word(late)}")
    if due_today:
        lines.extend(("", f"🟠 <b>Сегодня · {len(due_today)}</b>"))
        lines.extend(f"• {_task_link(task)}" for task in due_today)
    if upcoming:
        lines.extend(("", f"🟡 <b>Скоро · {len(upcoming)}</b>"))
        for task in upcoming:
            left = (task.due_date - today).days
            lines.append(
                f"• {_task_link(task)} — {task.due_date:%d.%m} · через {left} {_day_word(left)}"
            )
    return "\n".join(lines)


def render_task_overview(tasks: tuple[TaskDeadline, ...], today: date, days_ahead: int) -> str:
    selected = select_due_tasks(tasks, today, days_ahead)
    overdue = [task for task in selected if task.due_date < today]
    due_today = [task for task in selected if task.due_date == today]
    upcoming = [task for task in selected if task.due_date > today]
    weekday = _WEEKDAY_NAMES[today.weekday()]
    lines = [f"📋 <b>Задачи · {weekday}, {today.day} {_month_name(today.month)}</b>"]
    if overdue:
        lines.extend(("", f"🔴 <b>Просрочено · {len(overdue)}</b>"))
        for task in overdue:
            late = (today - task.due_date).days
            lines.append(f"• {_task_link(task)} — просрочена на {late} {_day_word(late)}")
    lines.extend(("", f"🟠 <b>Сегодня · {len(due_today)}</b>"))
    if due_today:
        lines.extend(f"• {_task_link(task)}" for task in due_today)
    else:
        lines.append("Дедлайнов на сегодня нет.")
    if upcoming:
        lines.extend(("", f"🟡 <b>Ближайшие {days_ahead} дней · {len(upcoming)}</b>"))
        for task in upcoming:
            left = (task.due_date - today).days
            lines.append(f"• {task.due_date:%d.%m} · {_task_link(task)}")
            lines.append(f"  └ {_remaining_days(left)}")
    elif days_ahead:
        lines.extend(("", f"На следующие {days_ahead} дн. дедлайнов нет."))
    return "\n".join(lines)


def _month_name(month: int) -> str:
    return (
        "января",
        "февраля",
        "марта",
        "апреля",
        "мая",
        "июня",
        "июля",
        "августа",
        "сентября",
        "октября",
        "ноября",
        "декабря",
    )[month - 1]


def _remaining_days(count: int) -> str:
    last_two = count % 100
    last = count % 10
    if 11 <= last_two <= 14:
        return f"осталось {count} дней"
    if last == 1:
        return f"остался {count} день"
    if 2 <= last <= 4:
        return f"осталось {count} дня"
    return f"осталось {count} дней"


class ReminderApplication:
    def __init__(
        self,
        repository: ReminderRepository,
        task_source: TaskDeadlineSource,
        sender: ReminderSender,
        chat_id: str,
        defaults: TaskDigestSettings,
    ):
        self.repository = repository
        self.task_source = task_source
        self.sender = sender
        self.chat_id = chat_id
        self.defaults = defaults

    def recover_inflight(self) -> None:
        self.repository.recover_inflight()

    def accepts_message(self, text: str) -> bool:
        command = text.strip().split(maxsplit=1)
        name = command[0].split("@", 1)[0].casefold() if command else ""
        return name in {
            "/tasks",
            "/tasksettings",
            "/reminders",
            "/cancelreminder",
            "/remind",
        } or is_reminder_request(text)

    def handle_message(self, update_id: int, text: str, now: datetime) -> str | None:
        command = text.strip().split(maxsplit=1)
        command_name = command[0].split("@", 1)[0].casefold() if command else ""
        if command_name == "/tasks":
            return self.task_overview(now)
        if command_name == "/tasksettings":
            return self._task_settings("" if len(command) == 1 else command[1])
        if command_name == "/reminders":
            return self._list_personal(now)
        if command_name == "/cancelreminder":
            return self._cancel_personal(command[1] if len(command) > 1 else "")
        if not is_reminder_request(text):
            return None
        result = parse_reminder(text, now)
        if isinstance(result, ReminderParseError):
            return (
                result.message
                + "\nПримеры: «Напомни завтра вечером позвонить», «Напомни через 2 часа проверить духовку», «Напомни 12.10 в 16:30 отправить файл»."
            )
        if not isinstance(result, ParsedReminder):
            return None
        reminder = self.repository.create_personal(update_id, result.text, result.due_at, now)
        local_due = reminder.due_at.astimezone(now.tzinfo)
        return f"🔔 Напомню #{reminder.id}: {reminder.text}\n{local_due:%d.%m.%Y в %H:%M}.\nОтменить: `/cancelreminder {reminder.id}`"

    def task_overview(self, now: datetime) -> str:
        settings = self.repository.digest_settings(self.defaults)
        return render_task_overview(self.task_source.tasks(), now.date(), settings.days_ahead)

    def run_task_digest(self, now: datetime) -> int:
        settings = self.repository.digest_settings(self.defaults)
        local_now = now
        local_day = local_now.date()
        if not settings.enabled or local_now.weekday() not in settings.weekdays:
            return 0
        if local_now.time().replace(tzinfo=None) < settings.send_time:
            return 0
        state = self.repository.digest(local_day)
        if state is None:
            text = render_task_digest(self.task_source.tasks(), local_day, settings.days_ahead)
            if not text:
                self.repository.skip_digest(local_day)
                return 0
            self.repository.prepare_digest(local_day, text, now)
            state = self.repository.digest(local_day)
        if state is None or state[0] != "pending":
            return 0
        if state[2] is not None and state[2] > now:
            return 0
        if not self.repository.claim_digest(local_day, now):
            return 0
        try:
            self.sender.send(self.chat_id, state[1], parse_mode="HTML")
        except ReminderSendRejected:
            self.repository.finish_digest(local_day, "pending", now + timedelta(minutes=5))
            raise
        except ReminderSendUncertain:
            self.repository.finish_digest(local_day, "uncertain")
            raise
        except Exception:
            self.repository.finish_digest(local_day, "uncertain")
            raise ReminderSendUncertain("Reminder delivery outcome is unknown") from None
        self.repository.finish_digest(local_day, "sent")
        return 1

    def run_due_personal(self, now: datetime) -> int:
        reminder = self.repository.claim_due_personal(now)
        if reminder is None:
            return 0
        local_due = reminder.due_at.astimezone(now.tzinfo)
        message = (
            f"⏰ Напоминание\n\n{reminder.text}\n\nПланировалось на {local_due:%d.%m.%Y в %H:%M}."
        )
        try:
            self.sender.send(self.chat_id, message)
        except ReminderSendRejected:
            self.repository.finish_personal(reminder.id, "pending", now + timedelta(minutes=5))
            raise
        except ReminderSendUncertain:
            self.repository.finish_personal(reminder.id, "uncertain")
            raise
        except Exception:
            self.repository.finish_personal(reminder.id, "uncertain")
            raise ReminderSendUncertain("Reminder delivery outcome is unknown") from None
        self.repository.finish_personal(reminder.id, "sent")
        return 1

    def _task_settings(self, arguments: str) -> str:
        settings = self.repository.digest_settings(self.defaults)
        parts = arguments.split()
        if not parts:
            return _format_settings(settings) + "\n\n" + _DIGEST_HELP
        command = parts[0].casefold()
        try:
            if command in {"on", "off"} and len(parts) == 1:
                settings = replace(settings, enabled=command == "on")
            elif command == "horizon" and len(parts) == 2:
                days = int(parts[1])
                if not 0 <= days <= 365:
                    raise ValueError
                settings = replace(settings, days_ahead=days)
            elif command == "schedule" and len(parts) in (3, 4):
                if parts[1].casefold() == "daily" and len(parts) == 3:
                    weekdays = tuple(range(7))
                    time_token = parts[2]
                elif parts[1].casefold() == "days" and len(parts) == 4:
                    weekdays = _parse_weekdays(parts[2])
                    time_token = parts[3]
                else:
                    raise ValueError
                send_time = time.fromisoformat(time_token)
                if send_time.second or send_time.microsecond:
                    raise ValueError
                settings = replace(settings, weekdays=weekdays, send_time=send_time)
            else:
                raise ValueError
        except ValueError:
            return "Не понял настройки.\n" + _DIGEST_HELP
        self.repository.save_digest_settings(settings)
        return "✅ Настройки сохранены.\n" + _format_settings(settings)

    def _list_personal(self, now: datetime) -> str:
        reminders = self.repository.pending_personal()
        if not reminders:
            return "Активных личных напоминаний нет. Создай: «Напомни завтра вечером позвонить»; настройки: `/tasksettings`."
        lines = ["🔔 Личные напоминания:"]
        for reminder in reminders:
            due = reminder.due_at.astimezone(now.tzinfo)
            status = " (нужна проверка доставки)" if reminder.status == "uncertain" else ""
            cancel = f" — `/cancelreminder {reminder.id}`" if reminder.status == "pending" else ""
            lines.append(
                f"• #{reminder.id} — {due:%d.%m.%Y в %H:%M}: {reminder.text}{status}{cancel}"
            )
        return "\n".join(lines)

    def _cancel_personal(self, raw_id: str) -> str:
        try:
            reminder_id = int(raw_id)
        except ValueError:
            return "Укажи номер, например `/cancelreminder 3`. Список: `/reminders`."
        if self.repository.cancel_personal(reminder_id):
            return f"Напоминание #{reminder_id} отменено."
        return (
            f"Не нашёл активное напоминание #{reminder_id}; проверь список командой `/reminders`."
        )


def _format_settings(settings: TaskDigestSettings) -> str:
    days = (
        "каждый день"
        if settings.weekdays == tuple(range(7))
        else ", ".join(_WEEKDAY_NAMES[weekday] for weekday in settings.weekdays)
    )
    horizon = (
        "только просроченные и на сегодня"
        if settings.days_ahead == 0
        else f"предупреждать за {settings.days_ahead} дн."
    )
    enabled = "включён" if settings.enabled else "выключен"
    return f"Дайджест {enabled}; {days}, {settings.send_time:%H:%M}; {horizon}."


def _parse_weekdays(value: str) -> tuple[int, ...]:
    try:
        parsed = {
            _WEEKDAY_INPUT[day.strip().casefold()]
            for day in re.split(r"[,;]+", value)
            if day.strip()
        }
    except KeyError:
        raise ValueError("Invalid weekday") from None
    if not parsed:
        raise ValueError("At least one weekday is required")
    return tuple(sorted(parsed))


def _task_link(task: TaskDeadline) -> str:
    title = escape(task.title)
    return f'<a href="{escape(task.url, quote=True)}">{title}</a>' if task.url else title


def _day_word(count: int) -> str:
    last_two = count % 100
    last = count % 10
    if 11 <= last_two <= 14:
        return "дней"
    if last == 1:
        return "день"
    if 2 <= last <= 4:
        return "дня"
    return "дней"
