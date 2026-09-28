import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from dontanello.entrypoints.telegram import TelegramCommands
from dontanello.modules.reports import DeliveryRejected, DeliveryService, DeliveryUncertain
from dontanello.modules.reports.adapters.sqlite_delivery import SQLiteDeliveryStore
from dontanello.platform.telegram_cursor import TelegramCursor


class FakeTelegram:
    def __init__(self):
        self.items = []
        self.sent = []
        self.error = None

    def updates(self, offset):
        return [item for item in self.items if item["update_id"] >= offset]

    def send_message(self, chat_id, text):
        if self.error:
            raise self.error
        self.sent.append((chat_id, text))
        return len(self.sent)


def update(identifier, chat_id=123, kind="private", command="/week"):
    return {
        "update_id": identifier,
        "message": {
            "chat": {"id": chat_id, "type": kind},
            "from": {"is_bot": False},
            "text": command,
        },
    }


class TelegramCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.telegram = FakeTelegram()
        self.periods = []
        self.now = datetime(2026, 9, 28, 19, 0, tzinfo=ZoneInfo("Asia/Bishkek"))
        self.cursor = TelegramCursor(root / "telegram_cursor.json")
        self.delivery = DeliveryService(
            SQLiteDeliveryStore(root / "reports.sqlite3"), self.telegram, "123"
        )

        def build(period):
            self.periods.append(period)
            return "report"

        self.commands = TelegramCommands(
            self.telegram,
            self.cursor,
            "123",
            self.delivery,
            build,
            lambda: self.now,
            lambda: "status",
        )

    def test_other_users_and_groups_cannot_read_private_reports(self):
        self.telegram.items = [update(1, chat_id=999), update(2, kind="group")]
        self.commands.run()
        self.assertEqual(self.periods, [])
        self.assertEqual(self.telegram.sent, [])
        self.assertEqual(self.cursor.load(), 3)

    def test_owner_commands_use_previous_complete_periods(self):
        self.telegram.items = [update(1), update(2, command="/month")]
        self.commands.run()
        self.assertEqual([p.start.isoformat() for p in self.periods], ["2026-09-21", "2026-08-01"])
        self.assertEqual(len(self.telegram.sent), 2)

    def test_cursor_failure_after_delivery_does_not_send_twice(self):
        self.telegram.items = [update(1)]
        with patch.object(self.cursor, "save", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                self.commands.run()
        self.commands.run()
        self.assertEqual(len(self.telegram.sent), 1)
        self.assertEqual(len(self.periods), 1)

    def test_rejected_send_stays_queued_during_backoff_then_retries(self):
        self.telegram.items = [update(1)]
        self.telegram.error = DeliveryRejected("rejected")
        with self.assertRaises(DeliveryRejected):
            self.commands.run()
        self.assertEqual(self.cursor.load(), 0)
        self.telegram.error = None
        with self.assertRaises(RuntimeError):
            self.commands.run()
        self.assertEqual(self.cursor.load(), 0)
        self.now = self.now.replace(minute=6)
        self.commands.run()
        self.assertEqual(len(self.telegram.sent), 1)
        self.assertEqual(self.cursor.load(), 2)
        self.assertEqual(len(self.periods), 1)

    def test_ambiguous_delivery_is_not_repeated_on_update_replay(self):
        self.telegram.items = [update(1)]
        self.telegram.error = DeliveryUncertain("acknowledgement lost")
        with self.assertRaises(DeliveryUncertain):
            self.commands.run()
        self.telegram.error = None
        self.commands.run()
        self.assertEqual(self.telegram.sent, [])
        self.assertEqual(self.cursor.load(), 2)
