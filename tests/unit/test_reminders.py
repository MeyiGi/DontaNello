import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dontanello.modules.reminders.adapters.sqlite import SQLiteReminderRepository
from dontanello.modules.reminders.application import (
    ReminderApplication,
    render_task_digest,
    render_task_overview,
    select_due_tasks,
)
from dontanello.modules.reminders.models import (
    ReminderSendRejected,
    ReminderSendUncertain,
    TaskDeadline,
    TaskDigestSettings,
)
from dontanello.modules.reminders.parser import parse_reminder


class TaskSource:
    def __init__(self, tasks=()):
        self.values = tuple(tasks)
        self.calls = 0

    def tasks(self):
        self.calls += 1
        return self.values


class Sender:
    def __init__(self):
        self.sent = []
        self.error = None

    def send(self, chat_id, text, *, parse_mode=None):
        if self.error:
            raise self.error
        self.sent.append((chat_id, text))
        return len(self.sent)


class ReminderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repository = SQLiteReminderRepository(Path(self.temp.name) / "reminders.sqlite3")
        self.sender = Sender()
        self.source = TaskSource()
        self.now = datetime(2026, 10, 4, 6, 0, tzinfo=ZoneInfo("Asia/Bishkek"))
        self.defaults = TaskDigestSettings()
        self.app = ReminderApplication(
            self.repository, self.source, self.sender, "private-chat", self.defaults
        )

    def test_digest_includes_overdue_today_and_configured_horizon_only(self):
        tasks = (
            TaskDeadline("late", "Просроченная", date(2026, 9, 29)),
            TaskDeadline("today", "Сегодня", date(2026, 10, 4)),
            TaskDeadline("soon", "Через неделю", date(2026, 10, 11)),
            TaskDeadline("later", "Позже", date(2026, 10, 12)),
            TaskDeadline("done", "Готовая", date(2026, 10, 4), completed=True),
            TaskDeadline("cancel", "Отменённая", date(2026, 10, 4), cancelled=True),
            TaskDeadline("work", "Рабочая", date(2026, 10, 4), excluded_from_digest=True),
        )
        selected = select_due_tasks(tasks, self.now.date(), 7)
        self.assertEqual([task.id for task in selected], ["late", "today", "soon"])
        text = render_task_digest(tasks, self.now.date(), 7)
        self.assertIn("на 5 дней", text)
        self.assertIn("<b>Сегодня · 1</b>", text)
        self.assertIn("через 7 дней", text)
        self.assertNotIn("Позже", text)
        self.assertNotIn("Готовая", text)
        self.assertNotIn("Отменённая", text)
        self.assertNotIn("Рабочая", text)
        self.assertIn("<b>Скоро · 1</b>", text)
        self.assertNotIn("https://notion.test", text)

    def test_zero_horizon_keeps_overdue_and_due_today(self):
        tasks = (
            TaskDeadline("late", "Поздно", date(2026, 10, 3)),
            TaskDeadline("today", "Сегодня", date(2026, 10, 4)),
            TaskDeadline("tomorrow", "Завтра", date(2026, 10, 5)),
        )
        self.assertEqual(
            [task.id for task in select_due_tasks(tasks, self.now.date(), 0)],
            ["late", "today"],
        )

    def test_digest_escapes_task_title_and_links_task_name(self):
        text = render_task_digest(
            (
                TaskDeadline(
                    "id",
                    "Прочитать <тему> & сдать",
                    date(2026, 10, 5),
                    "https://notion.test/a?x=1&y=2",
                ),
            ),
            self.now.date(),
            7,
        )
        self.assertIn(
            '<a href="https://notion.test/a?x=1&amp;y=2">Прочитать &lt;тему&gt; &amp; сдать</a>',
            text,
        )

    def test_task_overview_shows_compact_clickable_tasks_and_remaining_days(self):
        tasks = (
            TaskDeadline(
                "late",
                "Отправить форму",
                date(2026, 10, 2),
                "https://notion.test/late",
            ),
            TaskDeadline("today", "Проверить презентацию", self.now.date()),
            TaskDeadline(
                "soon",
                "Сдать конспект",
                date(2026, 10, 7),
                "https://notion.test/soon",
            ),
            TaskDeadline("later", "Позже", date(2026, 10, 12)),
            TaskDeadline("done", "Выполнена", self.now.date(), completed=True),
        )

        text = render_task_overview(tasks, self.now.date(), 7)

        self.assertIn("воскресенье, 4 октября", text)
        self.assertIn("Просрочено · 1", text)
        self.assertIn("Сегодня · 1", text)
        self.assertIn("Ближайшие 7 дней · 1", text)
        self.assertIn('<a href="https://notion.test/late">Отправить форму</a>', text)
        self.assertIn(
            '<a href="https://notion.test/soon">Сдать конспект</a>\n  └ осталось 3 дня', text
        )
        self.assertNotIn("Позже", text)
        self.assertNotIn("Выполнена", text)
        self.assertIn("— просрочена на 2 дня", text)

    def test_task_overview_always_shows_today_even_when_no_deadlines_match(self):
        text = render_task_overview((), self.now.date(), 7)

        self.assertIn("воскресенье, 4 октября", text)
        self.assertIn("Сегодня · 0", text)
        self.assertIn("Дедлайнов на сегодня нет.", text)
        self.assertIn("На следующие 7 дн. дедлайнов нет.", text)

    def test_task_overview_explains_when_no_tasks_have_deadlines(self):
        self.source.values = ()

        text = self.app.handle_message(1, "/tasks", self.now)

        self.assertIn("Дедлайнов на сегодня нет.", text)
        self.assertEqual(self.source.calls, 1)

    def test_digest_runs_once_after_configured_time_on_selected_weekday(self):
        self.repository.save_digest_settings(
            TaskDigestSettings(weekdays=(6,), send_time=time(6, 0), days_ahead=7)
        )
        self.source.values = (TaskDeadline("one", "Проверить отчёт", date(2026, 10, 7)),)
        before = self.now.replace(hour=5, minute=59)
        self.assertEqual(self.app.run_task_digest(before), 0)
        self.assertEqual(self.app.run_task_digest(self.now), 1)
        self.assertEqual(self.app.run_task_digest(self.now + timedelta(minutes=2)), 0)
        self.assertEqual(len(self.sender.sent), 1)
        self.assertIn("через 3 дня", self.sender.sent[0][1])

    def test_digest_skips_empty_day_and_does_not_send_empty_message(self):
        self.assertEqual(self.app.run_task_digest(self.now), 0)
        self.assertEqual(self.app.run_task_digest(self.now + timedelta(minutes=1)), 0)
        self.assertEqual(self.source.calls, 1)
        self.assertEqual(self.sender.sent, [])

    def test_rejected_digest_send_retries_same_snapshot_after_backoff(self):
        self.source.values = (TaskDeadline("one", "Сдать работу", date(2026, 10, 5)),)
        self.sender.error = ReminderSendRejected("rejected")
        with self.assertRaises(ReminderSendRejected):
            self.app.run_task_digest(self.now)
        self.sender.error = None
        self.assertEqual(self.app.run_task_digest(self.now + timedelta(minutes=4)), 0)
        self.assertEqual(self.app.run_task_digest(self.now + timedelta(minutes=5)), 1)
        self.assertEqual(self.source.calls, 1)

    def test_settings_can_switch_days_time_horizon_and_enabled_state(self):
        text = self.app.handle_message(1, "/tasksettings schedule days пн,ср,пт 07:30", self.now)
        self.assertIn("понедельник, среда, пятница, 07:30", text)
        text = self.app.handle_message(2, "/tasksettings horizon 14", self.now)
        self.assertIn("за 14 дн.", text)
        text = self.app.handle_message(3, "/tasksettings off", self.now)
        self.assertIn("выключен", text)
        self.assertEqual(
            self.repository.digest_settings(self.defaults),
            TaskDigestSettings(False, (0, 2, 4), time(7, 30), 14),
        )

    def test_bad_settings_are_rejected_without_changing_saved_configuration(self):
        before = self.repository.digest_settings(self.defaults)
        result = self.app.handle_message(1, "/tasksettings schedule days xx 06:00", self.now)
        self.assertIn("Не понял настройки", result)
        result = self.app.handle_message(2, "/tasksettings horizon 500", self.now)
        self.assertIn("Не понял настройки", result)
        self.assertEqual(self.repository.digest_settings(self.defaults), before)

    def test_personal_reminder_parses_dayparts_explicit_times_and_relative_intervals(self):
        cases = (
            ("Напомни завтра утром позвонить маме", datetime(2026, 10, 5, 9, 0)),
            ("Напомни завтра в обед купить продукты", datetime(2026, 10, 5, 13, 0)),
            ("Напомни завтра после обеда отправить письмо", datetime(2026, 10, 5, 15, 0)),
            ("Напомни завтра вечером сделать звонок", datetime(2026, 10, 5, 19, 0)),
            ("Напомни завтра купить молоко", datetime(2026, 10, 5, 9, 0)),
            (
                "Напомни завтра в 9:00 показать свой проект план и фактов руководителю",
                datetime(2026, 10, 5, 9, 0),
            ),
            ("Напомни 06.10 в 16:30 проверить задачу", datetime(2026, 10, 6, 16, 30)),
            ("Напомни через 2 часа проверить духовку", datetime(2026, 10, 4, 8, 0)),
            ("Напомни через полчаса проверить духовку", datetime(2026, 10, 4, 6, 30)),
            ("Напомни через час проверить духовку", datetime(2026, 10, 4, 7, 0)),
            ("/remind через неделю проверить отчёт", datetime(2026, 10, 11, 9, 0)),
        )
        for request, due in cases:
            with self.subTest(request=request):
                result = parse_reminder(request, self.now)
                self.assertEqual(result.due_at, due.replace(tzinfo=self.now.tzinfo))
                self.assertTrue(result.text)

    def test_personal_reminder_defaults_to_nine_but_requires_reminder_text(self):
        parsed = parse_reminder("Напомни завтра купить хлеб", self.now)
        self.assertEqual(parsed.due_at, datetime(2026, 10, 5, 9, tzinfo=self.now.tzinfo))
        self.assertIn("о чём", parse_reminder("Напомни завтра в 18:00", self.now).message)
        self.assertIn(
            "уже прошло", parse_reminder("Напомни сегодня в 05:00 проверить", self.now).message
        )
        self.assertIsNone(parse_reminder("Сегодня хорошая погода", self.now))

    def test_personal_reminders_are_deduplicated_cancellable_and_survive_repository_restart(self):
        first = self.app.handle_message(101, "Напомни завтра вечером забрать посылку", self.now)
        again = self.app.handle_message(101, "Напомни завтра вечером забрать посылку", self.now)
        self.assertEqual(first, again)
        pending = self.repository.pending_personal()
        self.assertEqual(len(pending), 1)
        restarted = SQLiteReminderRepository(Path(self.temp.name) / "reminders.sqlite3")
        self.assertEqual(restarted.pending_personal(), pending)
        self.assertTrue(restarted.cancel_personal(pending[0].id))
        self.assertEqual(restarted.pending_personal(), ())

    def test_reminder_database_is_private(self):
        self.repository.digest_settings(self.defaults)
        database_path = Path(self.temp.name) / "reminders.sqlite3"
        self.assertEqual(database_path.stat().st_mode & 0o777, 0o600)

    def test_due_personal_reminder_sends_once(self):
        self.app.handle_message(101, "Напомни завтра утром позвонить", self.now)
        due = (self.now + timedelta(days=1)).replace(hour=9)
        self.assertEqual(self.app.run_due_personal(due), 1)
        self.assertEqual(self.app.run_due_personal(due), 0)
        self.assertEqual(len(self.sender.sent), 1)
        self.assertIn("позвонить", self.sender.sent[0][1])

    def test_due_personal_reminder_retry_and_uncertain_delivery_rules(self):
        self.app.handle_message(1, "Напомни завтра утром позвонить", self.now)
        due = (self.now + timedelta(days=1)).replace(hour=9)
        self.sender.error = ReminderSendRejected("rejected")
        with self.assertRaises(ReminderSendRejected):
            self.app.run_due_personal(due)
        self.sender.error = None
        self.assertEqual(self.app.run_due_personal(due + timedelta(minutes=5)), 1)

        self.app.handle_message(2, "Напомни послезавтра утром отправить отчёт", self.now)
        later = (self.now + timedelta(days=2)).replace(hour=9)
        self.sender.error = ReminderSendUncertain("unknown")
        with self.assertRaises(ReminderSendUncertain):
            self.app.run_due_personal(later)
        self.sender.error = None
        self.app.recover_inflight()
        self.assertEqual(self.app.run_due_personal(later + timedelta(minutes=10)), 0)
        self.assertIn("нужна проверка доставки", self.app.handle_message(3, "/reminders", later))


if __name__ == "__main__":
    unittest.main()
