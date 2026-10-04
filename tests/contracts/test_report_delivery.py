"""Delivery journal contract tests against the real SQLite adapter."""

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dontanello.modules.reports.adapters.sqlite_delivery import SQLiteDeliveryStore
from dontanello.modules.reports.delivery import (
    DeliveryRejected,
    DeliveryService,
    DeliveryUncertain,
    split_message,
)


class RecordingSender:
    def __init__(self, outcomes=()):
        self.outcomes = list(outcomes)
        self.sent = []
        self.parse_modes = []

    def send_message(self, chat_id: str, text: str, parse_mode: str | None = None) -> int:
        self.sent.append((chat_id, text))
        self.parse_modes.append(parse_mode)
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        return len(self.sent)


class ReportDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = SQLiteDeliveryStore(Path(self.tempdir.name) / "reports.sqlite3")
        self.now = datetime(2025, 2, 3, 9, tzinfo=timezone.utc)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_utf16_chunking_preserves_text_and_respects_limit(self):
        text = "a" * 3499 + "😀" + "b" * 3501
        chunks = split_message(text)
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(len(part.encode("utf-16-le")) // 2 <= 3500 for part in chunks))
        self.assertEqual(split_message(""), [""])

    def test_restart_resumes_only_unsent_chunks_from_first_snapshot(self):
        sender = RecordingSender((41, DeliveryRejected("busy")))
        service = DeliveryService(self.store, sender, "chat-1")
        text = "first part" * 400
        with self.assertRaises(DeliveryRejected):
            service.deliver("weekly", text, self.now)
        self.assertEqual(
            [chunk.status for chunk in self.store.chunks("chat-1\x1fweekly")],
            [
                "sent",
                "pending",
            ],
        )

        restarted = DeliveryService(self.store, sender, "chat-1")
        later = self.now + timedelta(minutes=5)
        self.assertEqual(restarted.deliver("weekly", "changed content", later), 1)
        self.assertEqual(len(sender.sent), 3)
        self.assertEqual(sender.sent[2][1], sender.sent[1][1])
        self.assertEqual(
            [chunk.status for chunk in self.store.chunks("chat-1\x1fweekly")],
            ["sent", "sent"],
        )

    def test_rejected_chunk_waits_five_minutes_before_retry(self):
        sender = RecordingSender((DeliveryRejected("busy"), 2))
        service = DeliveryService(self.store, sender, "chat-1")
        with self.assertRaises(DeliveryRejected):
            service.deliver("daily", "hello", self.now)
        self.assertFalse(service.needs_delivery("daily", self.now + timedelta(minutes=4)))
        self.assertEqual(service.deliver("daily", "hello", self.now + timedelta(minutes=5)), 1)

    def test_retry_uses_parse_mode_saved_with_first_delivery_snapshot(self):
        sender = RecordingSender((DeliveryRejected("busy"), 2))
        service = DeliveryService(self.store, sender, "chat-1")
        with self.assertRaises(DeliveryRejected):
            service.deliver("task-overview", "<b>Today</b>", self.now, parse_mode="HTML")

        restarted = DeliveryService(self.store, sender, "chat-1")
        self.assertEqual(
            restarted.deliver("task-overview", "changed", self.now + timedelta(minutes=5)), 1
        )
        self.assertEqual(sender.parse_modes, ["HTML", "HTML"])

    def test_backoff_on_earlier_chunk_keeps_later_chunks_in_order(self):
        sender = RecordingSender((DeliveryRejected("busy"), 2, 3))
        service = DeliveryService(self.store, sender, "chat-1")
        text = "x" * 8000
        with self.assertRaises(DeliveryRejected):
            service.deliver("ordered", text, self.now)
        self.assertEqual(len(sender.sent), 1)
        self.assertEqual(
            service.deliver("ordered", "replacement", self.now + timedelta(minutes=5)), 3
        )
        self.assertEqual(len(sender.sent), 4)
        self.assertEqual("".join(message for _, message in sender.sent[1:]), text)

    def test_uncertain_chunk_stops_later_chunks_and_is_terminal(self):
        sender = RecordingSender((DeliveryUncertain("timeout"),))
        service = DeliveryService(self.store, sender, "chat-1")
        with self.assertRaises(DeliveryUncertain):
            service.deliver("uncertain-middle", "z" * 8000, self.now)
        self.assertEqual(len(sender.sent), 1)
        self.assertFalse(service.needs_delivery("uncertain-middle", self.now + timedelta(days=1)))
        self.assertTrue(service.is_terminal("uncertain-middle"))
        self.assertEqual(
            service.deliver("uncertain-middle", "z" * 8000, self.now + timedelta(days=1)), 0
        )
        self.assertEqual(len(sender.sent), 1)

    def test_ambiguous_result_and_crash_state_are_never_resent(self):
        sender = RecordingSender((DeliveryUncertain("timeout"),))
        service = DeliveryService(self.store, sender, "chat-1")
        with self.assertRaises(DeliveryUncertain):
            service.deliver("unclear", "hello", self.now)
        self.assertFalse(service.needs_delivery("unclear", self.now + timedelta(days=1)))
        self.assertEqual(service.deliver("unclear", "hello", self.now + timedelta(days=1)), 0)

        self.store.prepare("chat-1\x1fcrashed", ["possibly accepted"], self.now)
        self.assertTrue(self.store.claim_chunk("chat-1\x1fcrashed", 0, self.now))
        restarted = DeliveryService(self.store, sender, "chat-1")
        self.assertFalse(restarted.needs_delivery("crashed", self.now + timedelta(hours=1)))
        self.assertEqual(self.store.chunks("chat-1\x1fcrashed")[0].status, "uncertain")

    def test_key_is_scoped_to_recipient_and_activation_date_persists(self):
        first = DeliveryService(self.store, RecordingSender(), "chat-a")
        second = DeliveryService(self.store, RecordingSender(), "chat-b")
        first.deliver("same", "one", self.now)
        second.deliver("same", "two", self.now)
        self.assertEqual(self.store.chunks("chat-a\x1fsame")[0].text, "one")
        self.assertEqual(self.store.chunks("chat-b\x1fsame")[0].text, "two")

        self.assertEqual(self.store.active_since(self.now), self.now.date())
        later = self.now + timedelta(days=2)
        self.assertEqual(self.store.active_since(later), self.now.date())

    def test_schema_migrates_existing_chunks_without_changing_delivery_state(self):
        path = Path(self.tempdir.name) / "legacy.sqlite3"
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE report_delivery (
                delivery_key TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            );
            CREATE TABLE report_delivery_chunk (
                delivery_key TEXT NOT NULL REFERENCES report_delivery(delivery_key),
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('pending', 'sending', 'sent', 'uncertain')),
                message_id INTEGER,
                next_attempt TEXT,
                PRIMARY KEY (delivery_key, chunk_index)
            );
            INSERT INTO report_delivery VALUES ('old', '2025-02-03T09:00:00+00:00');
            INSERT INTO report_delivery_chunk VALUES ('old', 0, 'saved text', 'sent', 77, NULL);
            PRAGMA user_version = 1;
            """
        )
        connection.close()

        chunk = SQLiteDeliveryStore(path).chunks("old")[0]
        self.assertEqual((chunk.text, chunk.status, chunk.message_id), ("saved text", "sent", 77))
        self.assertIsNone(chunk.parse_mode)
        check = sqlite3.connect(path)
        self.assertEqual(check.execute("PRAGMA user_version").fetchone()[0], 2)
        check.close()


if __name__ == "__main__":
    unittest.main()
