"""Contracts owned by the completion use case."""

from collections.abc import Iterable
from datetime import date
from typing import Protocol

from .models import Observation


class CompletionSource(Protocol):
    def observations(self) -> Iterable[Observation]: ...
    def is_checked(self, item_id: str) -> bool: ...
    def stamp(self, item_id: str, completed_on: date) -> None: ...


class CheckboxState(Protocol):
    def get(self, key: str) -> bool | None: ...
    def record(self, key: str, checked: bool) -> None: ...


class Clock(Protocol):
    def today(self) -> date: ...
