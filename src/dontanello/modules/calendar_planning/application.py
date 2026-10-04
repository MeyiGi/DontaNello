"""Calendar-backed personal time planning and confirmation rules."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any, Sequence

from .models import (
    CalendarEvent,
    CalendarNotConnected,
    CalendarUnavailable,
    InlineButton,
    PlannerResponse,
    PlanProposal,
    TimeSlot,
    format_slot,
    plan_event_id,
)
from .parser import parse_plan_request
from .ports import CalendarGateway, PlanningRepository, PlanningRequestInterpreter


class CalendarPlanningApplication:
    def __init__(
        self,
        repository: PlanningRepository,
        calendar: CalendarGateway,
        timezone: tzinfo,
        day_start: time = time(8),
        day_end: time = time(22),
        buffer_minutes: int = 15,
        interpreter: PlanningRequestInterpreter | None = None,
        timezone_name: str = "Asia/Bishkek",
    ):
        if day_start >= day_end or not 0 <= buffer_minutes <= 60:
            raise ValueError("Invalid calendar planning window")
        self.repository = repository
        self.calendar = calendar
        self.timezone = timezone
        self.timezone_name = timezone_name
        self.day_start = day_start
        self.day_end = day_end
        self.buffer = timedelta(minutes=buffer_minutes)
        self.interpreter = interpreter

    def accepts_message(self, text: str, now: datetime) -> bool:
        return parse_plan_request(text, now) is not None or (
            self.interpreter is not None and _looks_like_planning_request(text)
        )

    def handle_message(self, update_id: int, text: str, now: datetime) -> PlannerResponse | None:
        existing = self.repository.proposal_for_update(update_id)
        if existing is not None:
            return self._proposal_response(existing, now)
        request = parse_plan_request(text, now)
        if request is None and self.interpreter is not None and _looks_like_planning_request(text):
            try:
                request = self.interpreter.interpret(text, now)
            except Exception:
                return PlannerResponse(
                    "Не смог разобрать время. Напиши, например: «завтра 1,5 часа на диплом» "
                    "или «сегодня безопасность 19:00–20:30». Календарь не менял."
                )
        if request is None:
            return None
        proposal_id = hashlib.sha256(f"{update_id}:{text}".encode()).hexdigest()[:32]
        try:
            events = self.calendar.events(*self._calendar_range(request.day))
        except CalendarNotConnected:
            return PlannerResponse(
                "Сначала подключи Google Calendar на компьютере командой `dontanello --authorize-calendar`."
            )
        except CalendarUnavailable:
            return PlannerResponse(
                "Не удалось прочитать Google Calendar. Календарь не менял — попробуй позже."
            )

        if request.fixed_slot is not None:
            window_start, window_end = self._day_bounds(request.day)
            if request.fixed_slot.start < window_start or request.fixed_slot.end > window_end:
                return PlannerResponse(
                    f"Можно планировать только в окне {self.day_start:%H:%M}–{self.day_end:%H:%M}. "
                    "Календарь не менял."
                )
            conflicts = self._conflicts(request.fixed_slot, events, now)
            if conflicts:
                options = self._suggestions(
                    request.day,
                    request.duration_minutes,
                    now,
                    events,
                    not_before=request.fixed_slot.end,
                )
                if not options:
                    options = self._suggestions(request.day, request.duration_minutes, now, events)
                proposal = PlanProposal(
                    proposal_id,
                    update_id,
                    text,
                    request.title,
                    request.day,
                    request.duration_minutes,
                    options,
                    None,
                    "pending",
                )
                self.repository.save_proposal(proposal)
                return self._conflict_response(proposal, request.fixed_slot, conflicts)
            options = (request.fixed_slot,)
        else:
            options = self._suggestions(request.day, request.duration_minutes, now, events)
            if not options:
                return PlannerResponse(
                    f"Не нашёл свободный слот на {request.duration_minutes} минут в выбранном окне. "
                    "Google Calendar не менял."
                )
        proposal = PlanProposal(
            proposal_id,
            update_id,
            text,
            request.title,
            request.day,
            request.duration_minutes,
            options[:1],
            0,
            "pending",
        )
        self.repository.save_proposal(proposal)
        return self._proposal_response(proposal, now)

    def handle_callback(self, data: str, now: datetime) -> PlannerResponse:
        parts = data.split(":")
        if len(parts) < 3 or parts[0] != "p":
            return PlannerResponse("Эта кнопка уже недействительна.")
        proposal = self.repository.get_proposal(parts[1])
        if proposal is None:
            return PlannerResponse("Не нашёл это предложение. Отправь запрос ещё раз.")
        action = parts[2]
        if action == "cancel":
            if proposal.status in {"created", "deleting"}:
                return PlannerResponse(
                    "Событие уже добавлено. Используй кнопку «↩ Отменить» в подтверждении."
                )
            if proposal.status == "deleted":
                return PlannerResponse("Это событие уже удалено из календаря.")
            if proposal.status == "creating":
                return PlannerResponse(
                    "Создание ещё не подтверждено. Нажми «✅ Проверить и добавить»."
                )
            if proposal.status == "pending":
                proposal = self._save(proposal, status="cancelled")
            return PlannerResponse("Планирование отменено. В календаре ничего не менял.")
        if action == "more":
            return self._more_options(proposal, now)
        if action == "choose" and len(parts) == 4:
            try:
                index = int(parts[3])
            except ValueError:
                return PlannerResponse("Не получилось выбрать этот вариант. Попробуй ещё раз.")
            if not 0 <= index < len(proposal.options):
                return PlannerResponse("Этот вариант больше недоступен. Нажми «Другой вариант».")
            proposal = self._save(proposal, selected_index=index)
            return self._proposal_response(proposal, now)
        if action == "add":
            return self._add(proposal, now)
        if action == "undo":
            return self._undo(proposal)
        return PlannerResponse("Не понял действие этой кнопки.")

    def _add(self, proposal: PlanProposal, now: datetime) -> PlannerResponse:
        if proposal.status == "created":
            return self._created_response(proposal, now)
        if proposal.status not in {"pending", "creating"} or proposal.selected_index is None:
            return PlannerResponse("Сначала выбери свободное время.")
        slot = proposal.options[proposal.selected_index]
        try:
            events = self.calendar.events(*self._calendar_range(proposal.day))
        except CalendarNotConnected:
            return PlannerResponse(
                "Google Calendar не подключён. Подключи его командой `dontanello --authorize-calendar`."
            )
        except CalendarUnavailable:
            return PlannerResponse(
                "Не удалось перепроверить Google Calendar. Событие пока не создавал."
            )
        conflicts = self._conflicts(
            slot, tuple(event for event in events if event.id != proposal.event_id), now
        )
        if conflicts:
            suggestions = self._suggestions(
                proposal.day,
                proposal.duration_minutes,
                now,
                events,
                exclude=proposal.options,
            )
            refreshed = self._save(
                proposal, options=suggestions, selected_index=None, status="pending"
            )
            return self._conflict_response(refreshed, slot, conflicts)

        event_id = proposal.event_id or plan_event_id(proposal.id)
        proposal = self._save(proposal, status="creating", event_id=event_id)
        try:
            self.calendar.create_event(
                event_id, proposal.id, proposal.title, slot, self.timezone_name
            )
        except CalendarNotConnected:
            return PlannerResponse(
                "Google Calendar не подключён. Событие не создал. Подключи его командой `dontanello --authorize-calendar`."
            )
        except CalendarUnavailable:
            # The stable Google event ID makes a repeated confirmation idempotent.
            return PlannerResponse(
                "Не получил подтверждение от Google Calendar. Нажми «✅ Проверить и добавить» ещё раз: "
                "я проверю тот же ID события и не создам дубль.",
                ((self._button(proposal, "add", "✅ Проверить и добавить"),),),
            )
        proposal = self._save(proposal, status="created")
        return self._created_response(proposal, now)

    def _undo(self, proposal: PlanProposal) -> PlannerResponse:
        if proposal.status in {"cancelled", "deleted"}:
            return PlannerResponse("Это событие уже отменено.")
        if proposal.status not in {"created", "deleting"} or not proposal.event_id:
            return PlannerResponse("Не нашёл созданное этим запросом событие.")
        proposal = self._save(proposal, status="deleting")
        try:
            deleted = self.calendar.delete_created_event(proposal.event_id, proposal.id)
        except CalendarUnavailable:
            return PlannerResponse(
                "Не удалось подтвердить отмену. Попробуй нажать «Отменить» ещё раз."
            )
        if not deleted:
            return PlannerResponse("Не нашёл исходное событие. Другие события не тронуты.")
        self._save(proposal, status="deleted")
        return PlannerResponse("↩ Событие удалено из Google Calendar.")

    def _more_options(self, proposal: PlanProposal, now: datetime) -> PlannerResponse:
        if proposal.status != "pending":
            return PlannerResponse("Это предложение уже закрыто.")
        try:
            events = self.calendar.events(*self._calendar_range(proposal.day))
        except CalendarUnavailable:
            return PlannerResponse("Не удалось обновить свободные промежутки из Google Calendar.")
        options = self._suggestions(
            proposal.day,
            proposal.duration_minutes,
            now,
            events,
            exclude=proposal.options,
        )
        if not options:
            return PlannerResponse("Других свободных вариантов в этом окне не нашёл.")
        proposal = self._save(proposal, options=options, selected_index=None)
        lines = [f"🕐 Другие варианты · {proposal.title}"]
        rows = []
        for index, slot in enumerate(proposal.options[:3]):
            lines.append(f"• {format_slot(slot)}")
            rows.append(
                (self._button(proposal, f"choose:{index}", f"{format_slot(slot)} · выбрать"),)
            )
        rows.append((self._button(proposal, "cancel", "❌ Отмена"),))
        return PlannerResponse("\n".join(lines), tuple(rows))

    def _proposal_response(self, proposal: PlanProposal, now: datetime) -> PlannerResponse:
        if proposal.status == "cancelled":
            return PlannerResponse("Планирование отменено.")
        if proposal.status in {"created", "deleting"}:
            return self._created_response(proposal, now)
        if proposal.status == "deleted":
            return PlannerResponse("Событие удалено из Google Calendar.")
        if proposal.selected_index is None:
            return PlannerResponse("Выбери один из предложенных промежутков.")
        slot = proposal.options[proposal.selected_index]
        duration = _format_duration(proposal.duration_minutes)
        date_label = _date_label(proposal.day, now.date())
        text = (
            f"📚 {proposal.title} — {duration}\n\nПредлагаю на {date_label}:\n{format_slot(slot)}"
        )
        return PlannerResponse(
            text,
            (
                (
                    self._button(proposal, "add", "✅ Добавить"),
                    self._button(proposal, "more", "🕐 Другой вариант"),
                ),
                (self._button(proposal, "cancel", "❌ Отмена"),),
            ),
        )

    def _created_response(self, proposal: PlanProposal, now: datetime) -> PlannerResponse:
        if proposal.selected_index is None or not proposal.event_id:
            return PlannerResponse("Событие создано, но его время не найдено в истории.")
        slot = proposal.options[proposal.selected_index]
        return PlannerResponse(
            f"✅ Добавил в календарь\n\n{proposal.title}\n{_date_label(proposal.day, now.date())} · {format_slot(slot)}",
            ((self._button(proposal, "undo", "↩ Отменить"),),),
        )

    def _conflict_response(
        self,
        proposal: PlanProposal,
        slot: TimeSlot,
        conflicts: Sequence[CalendarEvent],
    ) -> PlannerResponse:
        title_list = ", ".join(event.title for event in conflicts[:3])
        lines = [f"⚠️ {format_slot(slot)} пересекается с: {title_list}"]
        if proposal.options:
            lines.append("\nПредлагаю вместо этого:")
            rows = []
            for index, option in enumerate(proposal.options[:3]):
                lines.append(f"• {format_slot(option)}")
                rows.append(
                    (self._button(proposal, f"choose:{index}", f"{format_slot(option)} · выбрать"),)
                )
            rows.append((self._button(proposal, "cancel", "❌ Отмена"),))
            return PlannerResponse("\n".join(lines), tuple(rows))
        lines.append("\nСвободных вариантов нужной длины в этом окне не нашёл.")
        return PlannerResponse("\n".join(lines))

    def _suggestions(
        self,
        day: date,
        duration_minutes: int,
        now: datetime,
        events: Sequence[CalendarEvent],
        exclude: Sequence[TimeSlot] = (),
        not_before: datetime | None = None,
    ) -> tuple[TimeSlot, ...]:
        window_start, window_end = self._day_bounds(day)
        if day == now.date():
            window_start = max(window_start, _round_up(now.astimezone(self.timezone), 15))
        blocked = []
        for event in events:
            start = max(window_start, event.start.astimezone(self.timezone) - self.buffer)
            end = min(window_end, event.end.astimezone(self.timezone) + self.buffer)
            if start < end:
                blocked.append((start, end))
        blocked.sort()
        merged: list[tuple[datetime, datetime]] = []
        for start, end in blocked:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        gaps = []
        cursor = window_start
        for start, end in merged:
            if cursor < start:
                gaps.append((cursor, start))
            cursor = max(cursor, end)
        if cursor < window_end:
            gaps.append((cursor, window_end))
        duration = timedelta(minutes=duration_minutes)
        step = timedelta(minutes=30)
        excluded = {(slot.start, slot.end) for slot in exclude}
        candidates = []
        for gap_start, gap_end in gaps:
            candidate = _round_up(gap_start, 15)
            while candidate + duration <= gap_end:
                slot = TimeSlot(candidate, candidate + duration)
                if (slot.start, slot.end) not in excluded and (
                    not_before is None or slot.start >= not_before
                ):
                    candidates.append(slot)
                candidate += step
        # Earlier free slots are preferred; the user can ask for later alternatives.
        return tuple(candidates[:3])

    def _conflicts(
        self,
        slot: TimeSlot,
        events: Sequence[CalendarEvent],
        now: datetime | None = None,
    ) -> tuple[CalendarEvent, ...]:
        conflicts = []
        for event in events:
            start = event.start.astimezone(self.timezone) - self.buffer
            end = event.end.astimezone(self.timezone) + self.buffer
            if slot.start < end and slot.end > start:
                conflicts.append(event)
        if now is not None and slot.start < now.astimezone(self.timezone):
            conflicts.append(CalendarEvent("past", "это время уже прошло", slot.start, slot.end))
        return tuple(conflicts)

    def _day_bounds(self, day: date) -> tuple[datetime, datetime]:
        return (
            datetime.combine(day, self.day_start, self.timezone),
            datetime.combine(day, self.day_end, self.timezone),
        )

    def _calendar_range(self, day: date) -> tuple[datetime, datetime]:
        start, end = self._day_bounds(day)
        return start - self.buffer, end + self.buffer

    def _button(self, proposal: PlanProposal, action: str, label: str) -> InlineButton:
        return InlineButton(label, f"p:{proposal.id}:{action}")

    def _save(self, proposal: PlanProposal, **changes: Any) -> PlanProposal:
        updated = replace(proposal, **changes)
        self.repository.save_proposal(updated)
        return updated


def _round_up(value: datetime, minutes: int) -> datetime:
    remainder = value.minute % minutes
    if remainder == 0 and value.second == 0 and value.microsecond == 0:
        return value
    return value.replace(second=0, microsecond=0) + timedelta(minutes=minutes - remainder)


def _format_duration(minutes: int) -> str:
    hours, remainder = divmod(minutes, 60)
    if hours and remainder:
        return f"{hours} ч {remainder} мин"
    if hours:
        return f"{hours} ч"
    return f"{remainder} мин"


def _date_label(day: date, today: date) -> str:
    if day == today:
        return f"сегодня, {day:%d.%m}"
    if day == today + timedelta(days=1):
        return f"завтра, {day:%d.%m}"
    return f"{day:%d.%m.%Y}"


def _looks_like_planning_request(text: str) -> bool:
    lowered = text.casefold()
    has_day = any(
        token in lowered
        for token in (
            "сегодня",
            "завтра",
            "послезавтра",
            "понедельник",
            "вторник",
            "сред",
            "четверг",
            "пятниц",
            "суббот",
            "воскресень",
            "выходн",
            "недел",
            "через",
            "числа",
        )
    )
    has_intent = any(
        token in lowered
        for token in (
            "время",
            "заплан",
            "заним",
            "позаним",
            "поработ",
            "поставь",
            "добавь",
            "найди",
            "выдели",
        )
    )
    return has_day and has_intent
