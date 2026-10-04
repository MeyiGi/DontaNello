import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from dontanello.entrypoints.telegram import PERSONAL_KEYBOARD, TelegramCommands
from dontanello.modules.calendar_planning import InlineButton, PlannerResponse
from dontanello.modules.reports import DeliveryRejected, DeliveryService, DeliveryUncertain
from dontanello.modules.reports.adapters.sqlite_delivery import SQLiteDeliveryStore
from dontanello.platform.telegram_cursor import TelegramCursor


class FakeTelegram:
    def __init__(self):
        self.items = []
        self.sent = []
        self.parse_modes = []
        self.markups = []
        self.answered_callbacks = []
        self.error = None

    def updates(self, offset):
        return [item for item in self.items if item["update_id"] >= offset]

    def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):
        if self.error:
            raise self.error
        self.sent.append((chat_id, text))
        self.parse_modes.append(parse_mode)
        self.markups.append(reply_markup)
        return len(self.sent)

    def answer_callback_query(self, callback_query_id):
        self.answered_callbacks.append(callback_query_id)


class FakeReminders:
    def __init__(self):
        self.calls = []

    def accepts_message(self, text, now=None, update_id=None):
        return text.startswith(("/tasks", "/tasksettings", "/reminders")) or text.startswith(
            "Напомни"
        )

    def handle_message(self, update_id, text, now):
        self.calls.append((update_id, text, now))
        return "reminder response"


class FakeInbox:
    def __init__(self):
        self.calls = []
        self.pending = False

    def accepts_message(self, text, now, update_id=None):
        return (
            text.startswith("/inbox")
            or text.startswith("Напиши в инбокс")
            or (self.pending and bool(text.strip()) and not text.startswith("/"))
        )

    def handle_message(self, update_id, text, now):
        self.calls.append((update_id, text, now))
        if text == "/inbox":
            self.pending = True
            return "Send note"
        if self.pending and not text.startswith("/"):
            self.pending = False
            return "saved note"
        return "inbox response"


class FakePlanning:
    def __init__(self):
        self.calls = []
        self.callback_calls = []
        self.availability_calls = []

    def accepts_message(self, text, now):
        return text.startswith("сегодня хочу")

    def handle_message(self, update_id, text, now):
        self.calls.append((update_id, text))
        return PlannerResponse(
            "suggested",
            ((InlineButton("Add", "p:proposal:add"),),),
        )

    def handle_callback(self, data, now):
        self.callback_calls.append(data)
        return PlannerResponse("created")

    def show_availability(self, day, now):
        self.availability_calls.append(day)
        return PlannerResponse(
            "availability",
            ((InlineButton("Tomorrow", "a:2026-10-05"),),),
        )


def update(identifier, chat_id=123, kind="private", command="/week"):
    return {
        "update_id": identifier,
        "message": {
            "chat": {"id": chat_id, "type": kind},
            "from": {"is_bot": False},
            "text": command,
        },
    }


def callback_update(identifier, callback_id="cb-1", chat_id=123, user_id=123, kind="private"):
    return {
        "update_id": identifier,
        "callback_query": {
            "id": callback_id,
            "from": {"id": user_id, "is_bot": False},
            "chat_instance": "instance",
            "data": "p:proposal:add",
            "message": {"chat": {"id": chat_id, "type": kind}},
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

    def test_personal_keyboard_buttons_route_to_existing_actions(self):
        reminders = FakeReminders()
        inbox = FakeInbox()
        planning = FakePlanning()
        self.commands.reminders = reminders
        self.commands.inbox = inbox
        self.commands.planning = planning
        self.telegram.items = [
            update(1, command="📋 Мои задачи"),
            update(2, command="📥 Inbox"),
            update(3, command="⏰ Напоминания"),
            update(4, command="⚙️ Настройки дедлайнов"),
            update(5, command="ℹ️ Статус"),
            update(6, command="📅 Свободное время"),
        ]

        self.commands.run()

        self.assertEqual(len(self.periods), 0)
        self.assertEqual(reminders.calls[0][1], "/tasks")
        self.assertEqual(inbox.calls[0][1], "/inbox")
        self.assertEqual(reminders.calls[1][1], "/reminders")
        self.assertEqual(reminders.calls[2][1], "/tasksettings")
        self.assertEqual(self.telegram.sent[4][1], "status")
        self.assertEqual(self.telegram.sent[5][1], "availability")
        self.assertEqual(planning.availability_calls, [self.now.date()])
        self.assertEqual(self.telegram.parse_modes[0], "HTML")

    def test_personal_keyboard_hides_automatic_reports_and_shows_calendar_availability(self):
        buttons = [button["text"] for row in PERSONAL_KEYBOARD["keyboard"] for button in row]
        self.assertEqual(
            buttons,
            [
                "📋 Мои задачи",
                "📥 Inbox",
                "📅 Свободное время",
                "⏰ Напоминания",
                "⚙️ Настройки дедлайнов",
                "ℹ️ Статус",
            ],
        )

    def test_start_replaces_stale_telegram_keyboard(self):
        self.telegram.items = [update(1, command="/start")]

        self.commands.run()

        self.assertEqual(self.telegram.markups[0], PERSONAL_KEYBOARD)

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

    def test_reminder_commands_are_restricted_to_configured_private_chat(self):
        reminders = FakeReminders()
        self.commands.reminders = reminders
        self.telegram.items = [
            update(1, chat_id=999, command="/tasksettings"),
            update(2, kind="group", command="Напомни завтра позвонить"),
        ]
        self.commands.run()
        self.assertEqual(reminders.calls, [])
        self.assertEqual(self.telegram.sent, [])

    def test_private_reminder_command_delegates_to_reminder_application(self):
        reminders = FakeReminders()
        self.commands.reminders = reminders
        self.telegram.items = [update(1, command="Напомни завтра позвонить")]
        self.commands.run()
        self.assertEqual(len(reminders.calls), 1)
        self.assertIn("reminder response", self.telegram.sent[0][1])

    def test_calendar_request_sends_inline_confirmation_without_side_effect(self):
        planning = FakePlanning()
        self.commands.planning = planning
        self.telegram.items = [update(1, command="сегодня хочу 1 час почитать")]

        self.commands.run()

        self.assertEqual(len(planning.calls), 1)
        self.assertEqual(self.telegram.sent[0][1], "suggested")
        self.assertEqual(
            self.telegram.markups[0],
            {"inline_keyboard": [[{"text": "Add", "callback_data": "p:proposal:add"}]]},
        )

    def test_callback_actions_are_private_and_durable(self):
        planning = FakePlanning()
        self.commands.planning = planning
        self.telegram.items = [
            callback_update(1, callback_id="denied", user_id=999),
            callback_update(2, callback_id="allowed"),
        ]

        self.commands.run()

        self.assertEqual(planning.callback_calls, ["p:proposal:add"])
        self.assertEqual(self.telegram.answered_callbacks, ["denied", "allowed"])
        self.assertEqual(self.telegram.sent[-1][1], "created")
        self.assertEqual(self.cursor.load(), 3)

    def test_help_menu_groups_personal_features_without_work_commands(self):
        self.telegram.items = [update(1, command="/help")]
        self.commands.run()
        menu = self.telegram.sent[0][1]
        self.assertIn("📅 СВОБОДНОЕ ВРЕМЯ", menu)
        self.assertNotIn("Неделя", menu)
        self.assertNotIn("Месяц", menu)
        self.assertIn("📥 INBOX", menu)
        self.assertIn("Запиши в инбокс", menu)
        self.assertIn("✅ МОИ ЗАДАЧИ", menu)
        self.assertIn("Настройки дедлайнов", menu)
        self.assertIn("⏰ НАПОМИНАНИЯ", menu)
        self.assertNotIn("Работа", menu)

    def test_inbox_capture_is_private_and_routes_to_inbox_application(self):
        inbox = FakeInbox()
        self.commands.inbox = inbox
        self.telegram.items = [
            update(1, command="Напиши в инбокс хочу узнать, что такое шифр"),
            update(2, chat_id=999, command="Напиши в инбокс чужая заметка"),
            update(3, kind="group", command="/inbox групповая заметка"),
        ]
        self.commands.run()
        self.assertEqual(len(inbox.calls), 1)
        self.assertEqual(inbox.calls[0][0:2], (1, "Напиши в инбокс хочу узнать, что такое шифр"))
        self.assertEqual(self.telegram.sent[0][1], "inbox response")

    def test_inbox_button_accepts_next_plain_message_and_returns_capture_confirmation(self):
        inbox = FakeInbox()
        self.commands.inbox = inbox
        self.telegram.items = [update(1, command="📥 Inbox"), update(2, command="Аффинный шифр")]

        self.commands.run()

        self.assertEqual([item[1] for item in inbox.calls], ["/inbox", "Аффинный шифр"])
        self.assertEqual([item[1] for item in self.telegram.sent], ["Send note", "saved note"])

    def test_reminder_phrase_keeps_priority_while_inbox_prompt_is_pending(self):
        inbox = FakeInbox()
        reminders = FakeReminders()
        self.commands.inbox = inbox
        self.commands.reminders = reminders
        self.telegram.items = [
            update(1, command="📥 Inbox"),
            update(2, command="Напомни завтра позвонить"),
        ]

        self.commands.run()

        self.assertEqual([item[1] for item in reminders.calls], ["Напомни завтра позвонить"])
        self.assertEqual(self.telegram.sent[-1][1], "reminder response")

    def test_help_menu_does_not_expose_work_commands(self):
        self.telegram.items = [update(1, command="/help")]
        self.commands.run()
        menu = self.telegram.sent[0][1].casefold()
        for work_action in (
            "план на сегодня",
            "заявки",
            "отчет сейчас",
            "добавить заметку",
            "удалить заметку",
        ):
            self.assertNotIn(work_action, menu)
