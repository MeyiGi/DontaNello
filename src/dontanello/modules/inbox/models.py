"""Inbox capture records and outcomes."""

from dataclasses import dataclass


class InboxWriteRejected(Exception):
    """Notion explicitly rejected the page creation request."""


@dataclass(frozen=True)
class InboxCapture:
    update_id: int
    title: str
    status: str
    page_url: str = ""


@dataclass(frozen=True)
class InboxPage:
    title: str
    url: str
