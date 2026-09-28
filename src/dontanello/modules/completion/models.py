"""Internal types without provider-specific payloads."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Observation:
    key: str
    item_id: str
    checked: bool
