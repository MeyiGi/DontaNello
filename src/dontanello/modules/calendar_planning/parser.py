"""Small deterministic parser for common Russian time-planning requests."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

from .models import PlanRequest, TimeSlot

_RANGE = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})\s*[-–—]\s*(\d{1,2}):(\d{2})(?!\d)")
_DATE = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?(?!\d)")
_DURATION = re.compile(
    r"(?<!\w)(?:(\d+(?:[.,]\d+)?)\s*(час(?:а|ов|ик)?|ч\.?|h|мин(?:ут(?:ы|у)?)?)"
    r"|(полтора\s+часа|пол\s+часа|полчаса|один\s+час(?:ик)?|час(?:ик)?|часа))(?!\w)",
    re.IGNORECASE,
)
_PLAN_INTENT = re.compile(
    r"\b(?:хочу|занима\w*|позанима\w*|учиться|поучиться|поработать|"
    r"изучать|изучить|подготовиться|выдели|найди|запланируй)\b",
    re.IGNORECASE,
)
_DATE_WORDS = {
    "сегодня": 0,
    "завтра": 1,
    "послезавтра": 2,
}
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
_TITLE_NOISE = re.compile(
    r"\b(?:сегодня|завтра|послезавтра|найди|подбери|предложи|поставь|добавь|"
    r"мне|хочу|время|на|в|с|до|час(?:а|ов)?|ч\.?|h|мин(?:ут(?:ы|у)?)?|"
    r"позаниматься|заниматься|заняться|учиться|поучиться|поработать|"
    r"изучать|изучить|подготовиться|подготовка)\b",
    re.IGNORECASE,
)


def parse_plan_request(text: str, now: datetime) -> PlanRequest | None:
    clean = text.strip()
    if not clean or clean.startswith("/"):
        return None
    day = _parse_day(clean, now.date())
    if day is None:
        return None
    range_match = _RANGE.search(clean)
    duration_match = _DURATION.search(clean)
    if range_match:
        try:
            start = time(int(range_match[1]), int(range_match[2]))
            end = time(int(range_match[3]), int(range_match[4]))
        except ValueError:
            return None
        start_dt = datetime.combine(day, start, tzinfo=now.tzinfo)
        end_dt = datetime.combine(day, end, tzinfo=now.tzinfo)
        minutes = int((end_dt - start_dt).total_seconds() // 60)
        fixed_slot = TimeSlot(start_dt, end_dt)
    elif duration_match:
        minutes = _duration_minutes(duration_match)
        fixed_slot = None
    else:
        return None
    if not 15 <= minutes <= 480:
        return None
    if fixed_slot and (fixed_slot.end <= fixed_slot.start or day < now.date()):
        return None
    title = _parse_title(clean, range_match, duration_match)
    if not title:
        return None
    return PlanRequest(day, title[:120], minutes, fixed_slot)


def parse_plan_intent(text: str, now: datetime) -> tuple[date, str] | None:
    """Extract a dated activity when the user has not said how long it takes."""
    clean = text.strip()
    if (
        not clean
        or clean.startswith("/")
        or not _PLAN_INTENT.search(clean)
        or _DURATION.search(clean)
        or _RANGE.search(clean)
    ):
        return None
    day = _parse_day(clean, now.date())
    title = _parse_title(clean, None, None)
    return (day, title[:120]) if day is not None and title else None


def parse_duration_answer(text: str) -> int | None:
    """Parse a short duration reply to a pending planning question."""
    clean = re.sub(r"^(?:на|примерно|около)\s+", "", text.strip(), flags=re.IGNORECASE)
    match = _DURATION.fullmatch(clean)
    if not match:
        return None

    minutes = _duration_minutes(match)
    return minutes if 15 <= minutes <= 480 else None


def _duration_minutes(match: re.Match[str]) -> int:
    if match[1] is None:
        word_duration = match[3].casefold().replace("ё", "е")
        if word_duration.startswith("полтора"):
            return 90
        if word_duration.startswith("пол ") or word_duration == "полчаса":
            return 30
        return 60
    try:
        amount = float(match[1].replace(",", "."))
    except ValueError:
        return 0
    unit = match[2].casefold()
    return int(amount if "мин" in unit else amount * 60)


def _parse_day(text: str, today: date) -> date | None:
    lowered = text.casefold()
    for word, offset in _DATE_WORDS.items():
        if re.search(rf"\b{word}\b", lowered):
            return today + timedelta(days=offset)
    numeric = _DATE.search(text)
    if numeric:
        day, month = int(numeric[1]), int(numeric[2])
        year = int(numeric[3]) if numeric[3] else today.year
        try:
            result = date(year, month, day)
        except ValueError:
            return None
        if numeric[3] is None and result < today:
            try:
                result = result.replace(year=today.year + 1)
            except ValueError:
                return None
        return result
    for word, weekday in _WEEKDAYS.items():
        if re.search(rf"\b{word}\b", lowered):
            days = (weekday - today.weekday()) % 7 or 7
            return today + timedelta(days=days)
    return None


def _parse_title(
    text: str,
    range_match: re.Match[str] | None,
    duration_match: re.Match[str] | None,
) -> str:
    title = text
    for match in (range_match, duration_match, _DATE.search(title)):
        if match:
            title = title.replace(match.group(0), " ", 1)
    for word in (*_DATE_WORDS, *_WEEKDAYS):
        title = re.sub(rf"\b{word}\b", " ", title, flags=re.IGNORECASE)
    title = _TITLE_NOISE.sub(" ", title)
    title = re.sub(r"\s+", " ", title).strip(" \t,.:;—–-!?«»\"'")
    title = re.sub(r"^(?:про|по|для)\s+", "", title, flags=re.IGNORECASE)
    return title[:1].upper() + title[1:] if title else ""
