"""Deterministic parsing for explicit personal task creation requests."""

from __future__ import annotations

import re
from datetime import date, timedelta

from .models import TaskDraft

_PREFIX = re.compile(
    r"^\s*(?:добавь|создай|добавить|создать|запиши)\s+(?:мне\s+)?"
    r"(?:задачу|задачу в задачи|в задачи)\b\s*[:—,-]?\s*(.*)$",
    re.IGNORECASE | re.DOTALL,
)
_DATE = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?(?!\d)")
_AFTER_DAYS = re.compile(r"через\s+(\d+)\s+(?:день|дня|дней)", re.IGNORECASE)
_DATE_WORDS = {"сегодня": 0, "завтра": 1, "послезавтра": 2}
_WEEKDAYS = {
    "понедельник": 0,
    "вторник": 1,
    "среду": 2,
    "среда": 2,
    "четверг": 3,
    "пятницу": 4,
    "пятница": 4,
    "субботу": 5,
    "суббота": 5,
    "воскресенье": 6,
}


def parse_task_draft(text: str, today: date) -> TaskDraft | None:
    match = _PREFIX.match(text)
    if match is None:
        return None
    title = re.sub(r"\s+", " ", match.group(1)).strip(" \t\r\n:—,-")
    if not title:
        return TaskDraft("")

    due = _parse_due(title, today)
    if due is not None:
        title = _DATE.sub(" ", title, count=1)
        title = _AFTER_DAYS.sub(" ", title)
        for word in (*_DATE_WORDS, *_WEEKDAYS):
            title = re.sub(rf"\b(?:до|к|на)?\s*{word}\b", " ", title, flags=re.IGNORECASE)
        title = re.sub(r"\b(?:до|к|на)\s*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+", " ", title).strip(" \t\r\n:—,-")
    return TaskDraft(title[:120], due)


def _parse_due(text: str, today: date) -> date | None:
    after = _AFTER_DAYS.search(text)
    if after:
        return today + timedelta(days=int(after[1]))
    for word, offset in _DATE_WORDS.items():
        if re.search(rf"\b{word}\b", text, re.IGNORECASE):
            return today + timedelta(days=offset)
    numeric = _DATE.search(text)
    if numeric:
        day, month = int(numeric[1]), int(numeric[2])
        year = int(numeric[3]) if numeric[3] else today.year
        try:
            value = date(year, month, day)
        except ValueError:
            return None
        if numeric[3] is None and value < today:
            value = value.replace(year=today.year + 1)
        return value
    for word, weekday in _WEEKDAYS.items():
        if re.search(rf"\b{word}\b", text, re.IGNORECASE):
            return today + timedelta(days=(weekday - today.weekday()) % 7 or 7)
    return None
