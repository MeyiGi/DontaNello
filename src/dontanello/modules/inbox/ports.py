"""Contracts owned by Inbox capture."""

from typing import Protocol

from .models import InboxCapture, InboxPage


class InboxCaptureStore(Protocol):
    def claim(self, update_id: int, title: str, created_at: str) -> InboxCapture: ...
    def finish(self, update_id: int, status: str, page_url: str = "") -> None: ...
    def recover_inflight(self) -> None: ...


class InboxWriter(Protocol):
    def create(self, title: str) -> InboxPage: ...
