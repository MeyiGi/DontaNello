"""Durable report delivery rules, independent of a transport or database."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol, Sequence


class DeliveryRejected(RuntimeError):
    """The transport definitely did not accept the message."""


class DeliveryUncertain(RuntimeError):
    """The transport may have accepted the message, so retrying is unsafe."""


class MessageSender(Protocol):
    def send_message(self, chat_id: str, text: str) -> int: ...


@dataclass(frozen=True)
class DeliveryChunk:
    index: int
    text: str
    status: str
    message_id: int | None = None
    next_attempt: datetime | None = None


class DeliveryStore(Protocol):
    def prepare(self, key: str, chunks: Sequence[str], now: datetime) -> None: ...
    def chunks(self, key: str) -> Sequence[DeliveryChunk]: ...
    def claim_chunk(self, key: str, index: int, now: datetime) -> bool: ...
    def mark_sent(self, key: str, index: int, message_id: int, now: datetime) -> None: ...
    def mark_pending(self, key: str, index: int, next_attempt: datetime) -> None: ...
    def mark_uncertain(self, key: str, index: int) -> None: ...
    def recover_stale_sending(self, key: str) -> None: ...


MAX_CHUNK_UTF16_UNITS = 3500
RETRY_DELAY_SECONDS = 5 * 60


def split_message(text: str, limit: int = MAX_CHUNK_UTF16_UNITS) -> list[str]:
    """Split without losing characters, counting UTF-16 code units per API."""
    if limit < 2:
        raise ValueError("limit must be at least 2 UTF-16 units")
    chunks: list[str] = []
    current: list[str] = []
    units = 0
    for character in text:
        width = len(character.encode("utf-16-le")) // 2
        if units + width > limit:
            chunks.append("".join(current))
            current = []
            units = 0
        current.append(character)
        units += width
    if current:
        chunks.append("".join(current))
    elif not chunks and not text:
        chunks.append("")
    return chunks


class DeliveryService:
    def __init__(self, store: DeliveryStore, sender: MessageSender, chat_id: str):
        self.store = store
        self.sender = sender
        self.chat_id = chat_id

    def _key(self, key: str) -> str:
        # A report key is scoped to its recipient even if storage is shared.
        return f"{self.chat_id}\x1f{key}"

    def needs_delivery(self, key: str, now: datetime) -> bool:
        scoped_key = self._key(key)
        self.store.recover_stale_sending(scoped_key)
        rows = self.store.chunks(scoped_key)
        if not rows:
            return True
        if any(row.status == "uncertain" for row in rows):
            return False
        return any(
            row.status == "pending" and (row.next_attempt is None or row.next_attempt <= now)
            for row in rows
        )

    def has_uncertainty(self, key: str) -> bool:
        """Return whether any chunk has an unknown transport outcome."""
        return any(row.status == "uncertain" for row in self.store.chunks(self._key(key)))

    def is_terminal(self, key: str) -> bool:
        """Whether the journal can safely be advanced past this report key."""
        rows = self.store.chunks(self._key(key))
        if not rows:
            return False
        return any(row.status == "uncertain" for row in rows) or all(
            row.status == "sent" for row in rows
        )

    def existing_text(self, key: str) -> str | None:
        """Reuse the durable snapshot without another Notion/LLM request."""
        chunks = self.store.chunks(self._key(key))
        return "".join(chunk.text for chunk in chunks) if chunks else None

    def deliver(self, key: str, text: str, now: datetime) -> int:
        scoped_key = self._key(key)
        # Insert once: retries always use the first durable content snapshot.
        self.store.prepare(scoped_key, split_message(text), now)
        self.store.recover_stale_sending(scoped_key)
        sent = 0
        for chunk in self.store.chunks(scoped_key):
            if chunk.status != "pending":
                if chunk.status != "sent":
                    break
                continue
            if chunk.next_attempt is not None and chunk.next_attempt > now:
                break
            if not self.store.claim_chunk(scoped_key, chunk.index, now):
                break
            try:
                message_id = self.sender.send_message(self.chat_id, chunk.text)
            except DeliveryRejected:
                self.store.mark_pending(
                    scoped_key,
                    chunk.index,
                    now + timedelta(seconds=RETRY_DELAY_SECONDS),
                )
                raise
            except DeliveryUncertain:
                self.store.mark_uncertain(scoped_key, chunk.index)
                raise
            except Exception:
                self.store.mark_uncertain(scoped_key, chunk.index)
                raise DeliveryUncertain("message delivery outcome is unknown") from None
            self.store.mark_sent(scoped_key, chunk.index, message_id, now)
            sent += 1
        return sent
