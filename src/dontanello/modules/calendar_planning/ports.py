"""Ports owned by the personal calendar planning capability."""

from datetime import datetime
from typing import Protocol

from .models import CalendarEvent, PlanProposal, PlanRequest, TimeSlot


class CalendarGateway(Protocol):
    def events(self, start: datetime, end: datetime) -> tuple[CalendarEvent, ...]: ...

    def create_event(
        self, event_id: str, proposal_id: str, title: str, slot: TimeSlot, timezone: str
    ) -> str: ...

    def delete_created_event(self, event_id: str, proposal_id: str) -> bool: ...


class PlanningRepository(Protocol):
    def proposal_for_update(self, update_id: int) -> PlanProposal | None: ...

    def get_proposal(self, proposal_id: str) -> PlanProposal | None: ...

    def save_proposal(self, proposal: PlanProposal) -> None: ...


class PlanningRequestInterpreter(Protocol):
    def interpret(self, text: str, now: datetime) -> PlanRequest | None: ...
