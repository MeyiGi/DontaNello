"""Capture short personal notes in the configured Notion Inbox."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from .models import InboxCapture, InboxWriteRejected
from .ports import InboxCaptureStore, InboxWriter

PROMPT_TTL = timedelta(minutes=10)
_CANCEL_WORDS = {"отмена", "cancel"}
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

    def has_pending_prompt(self, now: datetime) -> bool:
        return self.store.has_pending_prompt(now.timestamp())

    def clear_pending_prompt(self) -> None:
        self.store.clear_pending_prompt()

    @staticmethod
    def is_explicit_request(text: str) -> bool:
        return parse_inbox_request(text) is not None

    def accepts_message(self, text: str, now: datetime, update_id: int | None = None) -> bool:
        if update_id is not None and self.store.has_capture(update_id):
            return True
        if parse_inbox_request(text) is not None:
            return True
        clean_text = text.strip()
        return bool(
            clean_text
            and not clean_text.startswith("/")
            and self.store.has_pending_prompt(now.timestamp())
        )

    def handle_message(
        self,
        update_id: int,
        text: str,
        now: datetime,
        *,
        title_override: str | None = None,
    ) -> str | None:
        title = parse_inbox_request(text)
        if title == "":
            self.store.set_pending_prompt((now + PROMPT_TTL).timestamp())
            return (
                "Отправь следующим сообщением текст — сохраню его в Inbox и пришлю ссылку. "
                "Напиши «отмена», если передумаешь."
            )
        if title is None:
            if text.strip().casefold() in _CANCEL_WORDS:
                if self.store.has_pending_prompt(now.timestamp()):
                    self.store.clear_pending_prompt()
                    return "Хорошо, ввод заметки в Inbox отменён."
                return None
            title = re.sub(r"\s+", " ", text).strip()
            if title_override:
                title = _clean_title(title_override) or title
            capture = self.store.claim_pending(update_id, title, now.isoformat(), now.timestamp())
            if capture is None:
                return None
        else:
            if title_override and title:
                title = _clean_title(title_override) or title
            self.store.clear_pending_prompt()
            capture = self.store.claim(update_id, title, now.isoformat())
        return self._write_capture(capture)

    def save_title(self, update_id: int, title: str, now: datetime) -> str:
        clean_title = _clean_title(title)
        if not clean_title:
            return "Не получилось подготовить заметку. Ничего не записал."
        self.store.clear_pending_prompt()
        capture = self.store.claim(update_id, clean_title, now.isoformat())
        return self._write_capture(capture)

    def _write_capture(self, capture: InboxCapture) -> str:
        update_id = capture.update_id
        title = capture.title
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


def _clean_title(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip(" \t\r\n:—,-")[:120]
