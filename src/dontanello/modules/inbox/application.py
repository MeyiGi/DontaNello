"""Capture short personal notes in the configured Notion Inbox."""

from __future__ import annotations

import re
from datetime import datetime

from .models import InboxWriteRejected
from .ports import InboxCaptureStore, InboxWriter

_INBOX_REQUEST = re.compile(
    r"^\s*(?:/inbox(?:@\w+)?|(?:напиши|запиши|добавь|сохрани)\s+"
    r"(?:(?:мне|это|идею|заметку)\s+)?в\s+(?:мой\s+)?инбокс)"
    r"\b\s*[:—,-]?\s*(.*)$",
    re.IGNORECASE | re.DOTALL,
)


def parse_inbox_request(text: str) -> str | None:
    match = _INBOX_REQUEST.match(text)
    if not match:
        return None
    content = re.sub(r"\s+", " ", match.group(1)).strip(" \t\r\n:—,-")
    if content.casefold().startswith("что хочу узнать "):
        content = "Хочу узнать, " + content[len("что хочу узнать ") :].strip()
    return content


class InboxCaptureApplication:
    def __init__(self, store: InboxCaptureStore, writer: InboxWriter):
        self.store = store
        self.writer = writer

    def recover_inflight(self) -> None:
        self.store.recover_inflight()

    def accepts_message(self, text: str) -> bool:
        return parse_inbox_request(text) is not None

    def handle_message(self, update_id: int, text: str, now: datetime) -> str | None:
        title = parse_inbox_request(text)
        if title is None:
            return None
        if not title:
            return "Напиши, что сохранить: «В инбокс: хочу узнать, что такое аффинный шифр»."

        capture = self.store.claim(update_id, title, now.isoformat())
        if capture.status == "created":
            return _created_message(capture.title, capture.page_url)
        if capture.status == "uncertain":
            return _uncertain_message(capture.title)
        if capture.status == "rejected":
            return "Notion отклонил эту запись. Исправь текст и отправь новым сообщением."

        try:
            page = self.writer.create(title)
        except InboxWriteRejected:
            self.store.finish(update_id, "rejected")
            return "Notion не принял запись в Inbox. Проверь текст и попробуй ещё раз."
        except Exception:
            self.store.finish(update_id, "uncertain")
            return _uncertain_message(title)

        self.store.finish(update_id, "created", page.url)
        return _created_message(page.title, page.url)


def _created_message(title: str, url: str) -> str:
    return f"✅ Записал в Notion Inbox: {title}\n{url}"


def _uncertain_message(title: str) -> str:
    return (
        f"⚠️ Не могу подтвердить, сохранилась ли запись «{title}». "
        "Проверь Inbox перед повторной отправкой, чтобы не создать дубль."
    )
