"""Compare TimeSense with the existing public Russian date parser contracts.

Run with the isolated candidate environment and repository source on the path:

    PYTHONPATH=src /tmp/dontanello-timesense-env/bin/python \
        scripts/experiments/timesense_comparison.py

This is a read-only experiment. It does not change parser routing or production
dependencies. Rows deliberately include existing user-facing phrases and edge
cases whose current behavior must be preserved before any replacement is viable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from timesense import TimeConfig, TimeSenseParser

from dontanello.modules.calendar_planning.parser import (
    parse_plan_intent,
    parse_plan_request,
)
from dontanello.modules.reminders.parser import parse_reminder

ZONE = ZoneInfo("Asia/Bishkek")


@dataclass(frozen=True)
class Scenario:
    route: str
    text: str
    now: datetime


def _now(year: int = 2026, month: int = 10, day: int = 4, hour: int = 18) -> datetime:
    return datetime(year, month, day, hour, tzinfo=ZONE)


SCENARIOS = (
    # Existing calendar parser fixtures and closely related Russian variants.
    Scenario("calendar", "сегодня хочу 1.5 часа позаниматься безопасностью", _now()),
    Scenario("calendar", "Сегодня хочу позаниматься безопасностью 1,5 часа", _now()),
    Scenario("calendar", "сегодня хочу позаниматься инженерной экономикой полтора часа", _now()),
    Scenario("calendar", "Хочу завтра позаниматься информационной безопасностью", _now()),
    Scenario("calendar", "Хочу позаниматься информационной безопасностью в 09.10", _now()),
    Scenario("calendar", "сегодня 19:00-20:00 чтение", _now()),
    Scenario("calendar", "завтра 30 минут читать", _now()),
    Scenario("calendar", "сегодня хочу читать", _now()),
    Scenario("calendar", "в понедельник 2 часа подготовиться к экзамену", _now()),
    Scenario("calendar", "послезавтра 45 мин изучать Rust", _now()),
    Scenario("calendar", "сегодня хочу завтра 1 час читать", _now()),
    Scenario("calendar", "завтра в 19:00-20:00 читать", _now()),
    Scenario("calendar", "завтра 10 минут читать", _now()),
    Scenario("calendar", "31.12.2026 1 час подготовиться", _now(2026, 12, 31)),
    Scenario("calendar", "01.01 1 час подготовиться", _now(2026, 12, 31)),
    Scenario("calendar", "29.02 1 час подготовиться", _now(2028, 2, 28)),
    Scenario("calendar", "29.02.2027 1 час подготовиться", _now(2027, 2, 28)),
    Scenario("calendar", "30.02 1 час подготовиться", _now()),
    Scenario("calendar", "завтра 09:00-08:00 читать", _now()),
    Scenario("calendar", "завтра 9 часов читать", _now()),
    # Existing reminder fixtures and errors, with relative and calendar boundaries.
    Scenario("reminder", "Напомни завтра утром позвонить маме", _now()),
    Scenario("reminder", "Напомни завтра в обед купить продукты", _now()),
    Scenario("reminder", "Напомни завтра после обеда отправить письмо", _now()),
    Scenario("reminder", "Напомни завтра вечером сделать звонок", _now()),
    Scenario("reminder", "Напомни завтра купить хлеб", _now()),
    Scenario("reminder", "Напомни 06.10 в 16:30 проверить задачу", _now()),
    Scenario("reminder", "Напомни через 2 часа проверить духовку", _now()),
    Scenario("reminder", "Напомни через полчаса проверить духовку", _now()),
    Scenario("reminder", "Напомни через неделю проверить отчёт", _now()),
    Scenario("reminder", "Напомни в пятницу в 9:00 отправить отчёт", _now()),
    Scenario("reminder", "Напомни 31.12 в 09:00 поздравить команду", _now(2026, 12, 31)),
    Scenario("reminder", "Напомни 01.01 в 09:00 поздравить команду", _now(2026, 12, 31)),
    Scenario("reminder", "Напомни 29.02 проверить календарь", _now(2028, 2, 28)),
    Scenario("reminder", "Напомни 31.02 проверить календарь", _now()),
    Scenario("reminder", "Напомни завтра в 18:00", _now()),
    Scenario("reminder", "Напомни сегодня в 05:00 проверить", _now()),
    Scenario("reminder", "Напомни через 0 дней проверить", _now()),
    Scenario("reminder", "Напомни завтра через 2 дня проверить", _now()),
    Scenario("reminder", "Сегодня хорошая погода", _now()),
)


def _candidate(parser: TimeSenseParser, scenario: Scenario) -> dict[str, Any] | None:
    result = parser.parse(
        scenario.text,
        now=scenario.now,
        language="ru",
        tz=scenario.now.tzinfo,
    )
    if result is None:
        return None
    result_time = getattr(result, "datetime_at", None) or getattr(result, "start_at", None)
    return {
        "kind": type(result).__name__,
        "title": getattr(result, "title", None),
        "time": result_time.isoformat() if result_time else None,
        "duration_minutes": getattr(result, "duration_minutes", None),
        "end": getattr(result, "end_at", None).isoformat()
        if getattr(result, "end_at", None)
        else None,
    }


def _baseline(scenario: Scenario) -> dict[str, Any] | None:
    if scenario.route == "calendar":
        request = parse_plan_request(scenario.text, scenario.now)
        if request is not None:
            return {
                "kind": "PlanRequest",
                "date": request.day.isoformat(),
                "title": request.title,
                "duration_minutes": request.duration_minutes,
                "fixed_start": request.fixed_slot.start.isoformat() if request.fixed_slot else None,
                "fixed_end": request.fixed_slot.end.isoformat() if request.fixed_slot else None,
            }
        intent = parse_plan_intent(scenario.text, scenario.now)
        if intent is not None:
            day, title = intent
            return {"kind": "PlanIntent", "date": day.isoformat(), "title": title}
        return None

    result = parse_reminder(scenario.text, scenario.now)
    if result is None:
        return None
    if hasattr(result, "message"):
        return {"kind": "ReminderParseError", "error": result.message}
    return {
        "kind": "ParsedReminder",
        "title": result.text,
        "time": result.due_at.isoformat(),
    }


def _differs(route: str, baseline: dict[str, Any] | None, candidate: dict[str, Any] | None) -> bool:
    if baseline is None:
        return candidate is not None
    if baseline["kind"] == "ReminderParseError":
        # TimeSense has no equivalent user-facing error type. Returning no
        # parse is the only behavior that does not create a reminder.
        return candidate is not None
    if candidate is None:
        return True
    baseline_title = baseline.get("title", "")
    candidate_title = candidate.get("title", "")
    if baseline_title.casefold() != candidate_title.casefold():
        return True
    if route == "calendar":
        if baseline["date"] != (candidate.get("time") or "")[:10]:
            return True
        if baseline["kind"] == "PlanRequest":
            return any(
                baseline.get(key) != candidate.get(candidate_key)
                for key, candidate_key in (
                    ("duration_minutes", "duration_minutes"),
                    ("fixed_start", "time"),
                    ("fixed_end", "end"),
                )
            )
        return False
    return baseline["time"] != candidate.get("time")


def main() -> None:
    parser = TimeSenseParser(TimeConfig(timezone="Asia/Bishkek", default_hour_for_one=9))
    missing = 0
    semantic_differences = 0
    for index, scenario in enumerate(SCENARIOS, start=1):
        baseline = _baseline(scenario)
        candidate = _candidate(parser, scenario)
        if candidate is None:
            missing += 1
        differs = _differs(scenario.route, baseline, candidate)
        semantic_differences += int(differs)
        marker = "DIFF" if differs else "MATCH"
        print(f"{index:02d} {marker} [{scenario.route}] {scenario.text}")
        print(f"   existing: {baseline}")
        print(f"   TimeSense: {candidate}")
    print(
        f"TOTAL scenarios={len(SCENARIOS)} candidate_none={missing} "
        f"rows_requiring_review={semantic_differences}"
    )


if __name__ == "__main__":
    main()
