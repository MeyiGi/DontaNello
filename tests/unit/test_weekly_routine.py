"""The morning learning plan respects weekday, timezone and delivery state."""

import tempfile
import unittest
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dontanello.modules.reminders.adapters.sqlite import SQLiteReminderRepository
from dontanello.modules.reminders.models import ReminderSendRejected, ReminderSendUncertain
from dontanello.modules.reminders.weekly_routine import WeeklyRoutine, render_weekly_routine

TZ = ZoneInfo("Asia/Bishkek")
DAYS = (
    ("LeetCode — 30 минут", "Испанский — 10 минут"),
    ("Испанский — 10 минут",),
    ("Испанский — 5–10 минут",),
    ("Испанский — 10 минут",),
    ("Скрипка — 25 минут", "Испанский — 10 минут"),
    ("LeetCode — 40 минут", "Испанский — 10 минут"),
    ("LeetCode — 40 минут", "Скрипка — 30 минут", "Испанский — 15 минут"),
)


class Sender:
    def __init__(self):
        self.messages = []
        self.error = None

    def send(self, chat_id, text, *, parse_mode=None):
        if self.error:
            raise self.error
        self.messages.append((chat_id, text))
        return len(self.messages)


class WeeklyRoutineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "reminders.sqlite3"
        self.sender = Sender()
        self.app = self.make_app()

    def make_app(self):
        return WeeklyRoutine(
            SQLiteReminderRepository(self.path),
            self.sender,
            "123",
            TZ,
            time(6, 30),
            DAYS,
        )

    def test_weekday_selection_matches_the_seven_day_schedule(self):
        monday = date(2026, 10, 19)
        for index, activities in enumerate(DAYS):
            with self.subTest(weekday=index):
                message = render_weekly_routine(monday + timedelta(days=index), DAYS)
                for activity in activities:
                    self.assertIn(activity, message)
                if index not in (0, 5, 6):
                    self.assertNotIn("LeetCode", message)
                if index not in (4, 6):
                    self.assertNotIn("Скрипка", message)

    def test_send_at_0630_once_even_after_restart(self):
        now = datetime(2026, 10, 19, 6, 30, tzinfo=TZ)
        self.assertEqual(self.app.run(now - timedelta(minutes=1)), 0)
        self.assertEqual(self.app.run(now), 1)
        self.assertEqual(self.app.run(now + timedelta(minutes=2)), 0)
        self.assertEqual(self.make_app().run(now + timedelta(minutes=3)), 0)
        self.assertEqual(len(self.sender.messages), 1)
        self.assertIn("LeetCode — 30 минут", self.sender.messages[0][1])

    def test_utc_instant_uses_bishkek_local_date(self):
        now = datetime(2026, 10, 19, 0, 30, tzinfo=timezone.utc)
        self.assertEqual(self.app.run(now), 1)
        self.assertIn("Понедельник, 19.10.2026", self.sender.messages[0][1])

    def test_explicit_rejection_retries_but_uncertain_delivery_never_does(self):
        now = datetime(2026, 10, 19, 6, 30, tzinfo=TZ)
        self.sender.error = ReminderSendRejected("rejected")
        self.assertEqual(self.app.run(now), 0)
        self.sender.error = None
        self.assertEqual(self.app.run(now + timedelta(minutes=4)), 0)
        self.assertEqual(self.app.run(now + timedelta(minutes=5)), 1)
        self.assertEqual(len(self.sender.messages), 1)

        next_day = now + timedelta(days=1)
        self.sender.error = ReminderSendUncertain("may have sent")
        self.assertEqual(self.app.run(next_day), 0)
        self.sender.error = None
        self.assertEqual(self.make_app().run(next_day + timedelta(minutes=10)), 0)
        self.assertEqual(len(self.sender.messages), 1)

    def test_interrupted_send_is_not_blindly_retried(self):
        day = date(2026, 10, 19)
        now = datetime(2026, 10, 19, 6, 30, tzinfo=TZ)
        store = SQLiteReminderRepository(self.path)
        store.prepare_routine(day, "frozen message")
        self.assertTrue(store.claim_routine(day, now))
        self.assertEqual(self.make_app().run(now), 0)
        self.assertEqual(store.routine_state(day)[0], "uncertain")
        self.assertEqual(self.sender.messages, [])

    def test_missed_day_is_not_sent_in_the_afternoon(self):
        now = datetime(2026, 10, 19, 13, 0, tzinfo=TZ)
        self.assertEqual(self.app.run(now), 0)
        self.assertEqual(self.sender.messages, [])
        self.assertEqual(self.app.run(now.replace(day=20, hour=7)), 1)


if __name__ == "__main__":
    unittest.main()
