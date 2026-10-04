import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dontanello.modules.inbox import InboxCaptureApplication, parse_inbox_request
from dontanello.modules.inbox.adapters.sqlite import SQLiteInboxCaptureStore
from dontanello.modules.inbox.models import InboxPage, InboxWriteRejected


class Writer:
    def __init__(self):
        self.pages = []
        self.error = None

    def create(self, title):
        if self.error:
            raise self.error
        self.pages.append(title)
        return InboxPage(title, f"https://notion.test/{len(self.pages)}")


class InboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "inbox.sqlite3"
        self.store = SQLiteInboxCaptureStore(self.path)
        self.writer = Writer()
        self.app = InboxCaptureApplication(self.store, self.writer)
        self.now = datetime(2026, 10, 4, 12, 0, tzinfo=ZoneInfo("Asia/Bishkek"))

    def test_parses_natural_phrases_and_slash_command(self):
        self.assertEqual(
            parse_inbox_request("Напиши в инбокс что хочу узнать что такое афиновый шифр"),
            "Хочу узнать, что такое афиновый шифр",
        )
        self.assertEqual(
            parse_inbox_request("/inbox Идея для автоматизации"), "Идея для автоматизации"
        )
        self.assertIsNone(parse_inbox_request("Обычное сообщение"))

    def test_capture_writes_page_and_replay_returns_same_link_without_duplicate(self):
        first = self.app.handle_message(
            45, "Напиши в инбокс что хочу узнать что такое афиновый шифр", self.now
        )
        again = self.app.handle_message(
            45, "Напиши в инбокс что хочу узнать что такое афиновый шифр", self.now
        )
        self.assertEqual(self.writer.pages, ["Хочу узнать, что такое афиновый шифр"])
        self.assertEqual(first, again)
        self.assertIn("https://notion.test/1", first)

    def test_uncertain_capture_is_never_automatically_retried(self):
        self.writer.error = TimeoutError("ack lost")
        first = self.app.handle_message(46, "/inbox проверить идею", self.now)
        self.writer.error = None
        again = self.app.handle_message(46, "/inbox проверить идею", self.now)
        self.assertIn("Проверь Inbox", first)
        self.assertEqual(first, again)
        self.assertEqual(self.writer.pages, [])

    def test_rejected_capture_can_be_retried_as_a_new_message(self):
        self.writer.error = InboxWriteRejected()
        result = self.app.handle_message(47, "/inbox новая идея", self.now)
        self.assertIn("не принял", result)
        self.writer.error = None
        result = self.app.handle_message(48, "/inbox новая идея", self.now)
        self.assertIn("Записал", result)
        self.assertEqual(self.writer.pages, ["новая идея"])

    def test_restart_recovers_inflight_as_uncertain(self):
        self.store.claim(49, "Зависшая запись", self.now.isoformat())
        restarted = InboxCaptureApplication(SQLiteInboxCaptureStore(self.path), self.writer)
        restarted.recover_inflight()
        self.assertIn(
            "Проверь Inbox", restarted.handle_message(49, "/inbox Зависшая запись", self.now)
        )
        self.assertEqual(self.writer.pages, [])

    def test_capture_database_is_private(self):
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
