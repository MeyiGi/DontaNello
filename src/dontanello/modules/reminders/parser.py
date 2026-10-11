"""Deterministic parsing for common Russian one-time reminder phrases."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

from .models import ParsedReminder, ReminderDraft, ReminderParseError

_PREFIX = re.compile(r"^\s*(?:напомни(?:\s+мне)?|/remind(?:@\w+)?)\b", re.IGNORECASE)
_RELATIVE = re.compile(
    r"\bчерез\s+(?:(\d{1,3})\s+)?(полчаса|минут(?:у|ы)?|минут|час(?:а|ов)?|час|д(?:ень|ня|ней)|недел(?:ю|и|ь)|неделю)\b",
    re.IGNORECASE,
)
_EXPLICIT_DATE = re.compile(r"\b(\d{1,2})[./-](\d{1,2})(?:[./-](\d{4}))?\b")
_CLOCK = re.compile(r"\b(?:в|во)?\s*(\d{1,2}):(\d{2})\b", re.IGNORECASE)
_CLOCK_HOUR = re.compile(r"\b(?:в|во)\s+(\d{1,2})(?![\d:])\b", re.IGNORECASE)
_DAYPART_TIMES = {
    "early_morning": time(7),
    "morning": time(9),
    "noon": time(13),
    "afternoon": time(15),
    "evening": time(19),
    "night": time(22),
}
_DAYPARTS = (
    (re.compile(r"\bрано\s+утром\b", re.IGNORECASE), "early_morning"),
    (re.compile(r"\b(?:в\s+)?обед\b", re.IGNORECASE), "noon"),
    (re.compile(r"\bпосле\s+обеда\b", re.IGNORECASE), "afternoon"),
    (re.compile(r"\bдн[её]м\b", re.IGNORECASE), "afternoon"),
    (re.compile(r"\bутром\b", re.IGNORECASE), "morning"),
    (re.compile(r"\bвечером\b", re.IGNORECASE), "evening"),
    (re.compile(r"\bночью\b", re.IGNORECASE), "night"),
)
_WEEKDAYS = {
    "понедельник": 0,
    "понедельника": 0,
    "пн": 0,
    "вторник": 1,
    "вторника": 1,
    "вт": 1,
    "среду": 2,
    "среда": 2,
    "ср": 2,
    "четверг": 3,
    "четверга": 3,
    "чт": 3,
    "пятницу": 4,
    "пятница": 4,
    "пт": 4,
    "субботу": 5,
    "суббота": 5,
    "сб": 5,
    "воскресенье": 6,
    "воскресенья": 6,
    "вс": 6,
}
_WEEKDAY = re.compile(
    r"\b(?:в\s+)?(" + "|".join(sorted(_WEEKDAYS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def is_reminder_request(text: str) -> bool:
    return _PREFIX.match(text) is not None


def parse_reminder(text: str, now: datetime) -> ParsedReminder | ReminderParseError | None:
    """Parse a supported reminder; return None for ordinary chat messages."""
    prefix = _PREFIX.match(text)
    if prefix is None:
        return None
    remaining = text[prefix.end() :]
    spans: list[tuple[int, int]] = []
    relative = _RELATIVE.search(remaining)
    explicit_date = _EXPLICIT_DATE.search(remaining)
    simple_date = re.search(r"\bсегодня\b|\bзавтра\b|\bпослезавтра\b", remaining, re.I)
    weekday = _WEEKDAY.search(remaining)
    relative_delta: timedelta | None = None
    explicit_day: datetime | None = None
    if relative:
        unit = relative.group(2).casefold()
        count = int(relative.group(1)) if relative.group(1) else 1
        if count < 1:
            return ReminderParseError("Укажи положительный интервал, например «через 2 дня».")
        if unit == "полчаса":
            relative_delta = timedelta(minutes=30)
        elif unit.startswith("минут"):
            relative_delta = timedelta(minutes=count)
        elif unit.startswith("час"):
            relative_delta = timedelta(hours=count)
        elif unit.startswith("д"):
            relative_delta = timedelta(days=count)
        else:
            relative_delta = timedelta(weeks=count)
        spans.append(relative.span())
    elif explicit_date:
        day, month = int(explicit_date.group(1)), int(explicit_date.group(2))
        year = int(explicit_date.group(3)) if explicit_date.group(3) else now.year
        try:
            explicit_day = datetime(year, month, day, tzinfo=now.tzinfo)
        except ValueError:
            return ReminderParseError("Не распознал дату. Используй формат ДД.ММ или ДД.ММ.ГГГГ.")
        if explicit_date.group(3) is None and explicit_day.date() < now.date():
            explicit_day = explicit_day.replace(year=year + 1)
        spans.append(explicit_date.span())
    elif simple_date:
        token = simple_date.group().casefold()
        count = {"сегодня": 0, "завтра": 1, "послезавтра": 2}[token]
        explicit_day = datetime.combine(now.date() + timedelta(days=count), time.min, now.tzinfo)
        spans.append(simple_date.span())
    elif weekday:
        weekday_index = _WEEKDAYS[weekday.group(1).casefold()]
        days = (weekday_index - now.weekday()) % 7
        explicit_day = datetime.combine(now.date() + timedelta(days=days), time.min, now.tzinfo)
        spans.append(weekday.span())

    clock_match = _CLOCK.search(remaining)
    hour_match = _CLOCK_HOUR.search(remaining) if clock_match is None else None
    selected_time: time | None = None
    if clock_match:
        hour, minute = int(clock_match.group(1)), int(clock_match.group(2))
        if hour > 23 or minute > 59:
            return ReminderParseError("Время должно быть в формате 06:30 или 18:00.")
        selected_time = time(hour, minute)
        spans.append(clock_match.span())
    elif hour_match:
        hour = int(hour_match.group(1))
        if hour > 23:
            return ReminderParseError("Укажи время от 00:00 до 23:59.")
        selected_time = time(hour)
        spans.append(hour_match.span())

    daypart_match = None
    daypart_time: time | None = None
    for pattern, part_name in _DAYPARTS:
        match = pattern.search(remaining)
        if match:
            daypart_match, daypart_time = match, _DAYPART_TIMES[part_name]
            break
    if daypart_match:
        spans.append(daypart_match.span())
    if selected_time is None:
        selected_time = daypart_time or time(9, 0)
    due_at = _resolve_due_at(
        now,
        explicit_day,
        selected_time,
        daypart_time,
        relative_delta,
        explicit_clock=clock_match is not None or hour_match is not None,
    )
    if isinstance(due_at, ReminderParseError):
        return due_at

    content = _remove_spans(remaining, spans)
    content = re.sub(r"^[\s,;:—–-]+|[\s,;:—–-]+$", "", content).strip()
    content = re.sub(r"^(?:мне\s+)?(?:на\s+)?", "", content, flags=re.IGNORECASE).strip()
    if not content:
        return ReminderParseError("Напиши, о чём именно напомнить после даты и времени.")
    return ParsedReminder(content, due_at)


def resolve_reminder_draft(
    draft: ReminderDraft, now: datetime
) -> ParsedReminder | ReminderParseError:
    """Resolve a typed reminder request without parsing its text as a command."""
    if now.tzinfo is None or now.utcoffset() is None:
        return ReminderParseError("Для напоминания требуется время с часовым поясом.")
    content = draft.text.strip() if isinstance(draft.text, str) else ""
    if not content:
        return ReminderParseError("Напиши, о чём именно напомнить после даты и времени.")
    if draft.day is not None and (
        not isinstance(draft.day, date) or isinstance(draft.day, datetime)
    ):
        return ReminderParseError("Не распознал дату напоминания.")
    if draft.day is not None and draft.delay_minutes is not None:
        return ReminderParseError("Укажи дату или интервал, но не оба варианта сразу.")
    if draft.clock is not None and (
        not isinstance(draft.clock, time)
        or draft.clock.second != 0
        or draft.clock.microsecond != 0
        or draft.clock.tzinfo is not None
    ):
        return ReminderParseError("Время должно быть указано без секунд в формате ЧЧ:ММ.")
    if draft.daypart is not None and (
        not isinstance(draft.daypart, str) or draft.daypart not in _DAYPART_TIMES
    ):
        return ReminderParseError("Не распознал часть дня для напоминания.")
    if draft.delay_minutes is not None and (
        type(draft.delay_minutes) is not int or draft.delay_minutes <= 0
    ):
        return ReminderParseError("Укажи положительный интервал для напоминания.")

    try:
        explicit_day = (
            datetime.combine(draft.day, time.min, now.tzinfo) if draft.day is not None else None
        )
        relative_delta = (
            timedelta(minutes=draft.delay_minutes) if draft.delay_minutes is not None else None
        )
        daypart_time = _DAYPART_TIMES.get(draft.daypart) if draft.daypart is not None else None
        selected_time = draft.clock or daypart_time or time(9)
        due_at = _resolve_due_at(
            now,
            explicit_day,
            selected_time,
            daypart_time,
            relative_delta,
            explicit_clock=draft.clock is not None,
        )
    except OverflowError:
        return ReminderParseError("Укажи более короткий интервал для напоминания.")
    if isinstance(due_at, ReminderParseError):
        return due_at
    return ParsedReminder(content, due_at)


def _resolve_due_at(
    now: datetime,
    explicit_day: datetime | None,
    selected_time: time,
    daypart_time: time | None,
    relative_delta: timedelta | None,
    *,
    explicit_clock: bool,
) -> datetime | ReminderParseError:
    if (
        not explicit_clock
        and daypart_time is None
        and relative_delta is not None
        and relative_delta < timedelta(days=1)
        and explicit_day is None
    ):
        due_at = now + relative_delta
    else:
        if explicit_day is None:
            target_day = (
                (now + relative_delta).date()
                if relative_delta is not None and relative_delta >= timedelta(days=1)
                else now.date()
            )
            due_at = datetime.combine(target_day, selected_time, now.tzinfo)
            if due_at <= now:
                due_at += timedelta(days=1)
        else:
            target_day = explicit_day.date()
            if relative_delta is not None and relative_delta >= timedelta(days=1):
                target_day = (now + relative_delta).date()
            due_at = datetime.combine(target_day, selected_time, now.tzinfo)
    if due_at <= now:
        return ReminderParseError("Это время уже прошло. Укажи будущую дату и время.")
    return due_at


def _remove_spans(text: str, spans: list[tuple[int, int]]) -> str:
    result = text
    for start, end in sorted(set(spans), reverse=True):
        result = result[:start] + " " + result[end:]
    return result
