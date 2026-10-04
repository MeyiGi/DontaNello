import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from dontanello.modules.reports.adapters.sqlite_delivery import SQLiteDeliveryStore
from dontanello.modules.reports.application import previous_month, previous_week
from dontanello.modules.reports.delivery import DeliveryService, DeliveryUncertain
from dontanello.modules.reports.schedule import ScheduledReportError, ScheduledReports


class Sender:
    def __init__(self):
        self.messages = []

    def send_message(self, chat_id: str, text: str) -> int:
        self.messages.append((chat_id, text))
        return len(self.messages)


class ReportScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = TemporaryDirectory()
        store = SQLiteDeliveryStore(Path(self.tempdir.name) / "schedule.sqlite3")
        self.sender = Sender()
        self.delivery = DeliveryService(store, self.sender, "test-chat")
        self.built = []

    def tearDown(self):
        self.tempdir.cleanup()

    def scheduler(self, activated_on=date(2025, 1, 1), build=None):
        def default_build(period):
            self.built.append(period)
            return period.kind + " report"

        return ScheduledReports(
            delivery=self.delivery,
            build=build or default_build,
            activated_on=activated_on,
        )

    def test_week_and_month_are_both_due_on_first_monday_and_stable(self):
        scheduler = self.scheduler()
        now = datetime(2025, 9, 1, 9, tzinfo=timezone.utc)
        self.assertEqual(scheduler.run(now), 2)
        self.assertEqual([period.kind for period in self.built], ["week", "month"])
        self.assertEqual(
            [(period.start, period.end) for period in self.built],
            [(date(2025, 8, 25), date(2025, 9, 1)), (date(2025, 8, 1), date(2025, 9, 1))],
        )
        self.assertEqual(scheduler.run(now), 0)
        self.assertEqual(len(self.built), 2)
        self.assertEqual(len(self.sender.messages), 2)

    def test_before_schedule_hour_does_not_run_weekly_due_today(self):
        scheduler = self.scheduler()
        now = datetime(2025, 9, 8, 8, 59, tzinfo=timezone.utc)
        scheduler.run(now)
        weekly = [period for period in self.built if period.kind == "week"]
        self.assertEqual(len(weekly), 1)
        self.assertEqual(weekly[0], previous_week(date(2025, 9, 1)))

    def test_sunday_schedule_sends_previous_sunday_to_sunday_period(self):
        scheduler = ScheduledReports(
            self.delivery,
            lambda period: self.built.append(period) or "weekly",
            date(2025, 1, 1),
            weekly_weekday=6,
        )
        scheduler.run(datetime(2025, 9, 7, 9, tzinfo=timezone.utc))
        weekly = [period for period in self.built if period.kind == "week"]
        self.assertEqual(
            weekly,
            [previous_week(date(2025, 9, 7), weekday=6)],
        )

    def test_sunday_schedule_before_nine_waits_for_previous_sunday(self):
        scheduler = ScheduledReports(
            self.delivery,
            lambda period: self.built.append(period) or "weekly",
            date(2025, 1, 1),
            weekly_weekday=6,
        )
        scheduler.run(datetime(2025, 9, 7, 8, 59, tzinfo=timezone.utc))
        weekly = [period for period in self.built if period.kind == "week"]
        self.assertEqual(weekly, [previous_week(date(2025, 8, 31), weekday=6)])

    def test_activation_floor_skips_pre_activation_due_periods(self):
        scheduler = self.scheduler(activated_on=date(2025, 9, 2))
        now = datetime(2025, 9, 1, 10, tzinfo=timezone.utc)
        self.assertEqual(scheduler.run(now), 0)
        self.assertEqual(self.built, [])
        self.assertEqual(self.sender.messages, [])

    def test_downtime_catchup_runs_only_latest_weekly_and_monthly_periods(self):
        scheduler = self.scheduler()
        now = datetime(2025, 9, 18, 12, tzinfo=timezone.utc)
        self.assertEqual(scheduler.run(now), 2)
        self.assertEqual(
            self.built,
            [previous_week(date(2025, 9, 15)), previous_month(date(2025, 9, 1))],
        )

    def test_leap_february_and_year_boundary_periods(self):
        self.assertEqual(
            previous_month(date(2024, 3, 1)),
            type(previous_month(date(2024, 3, 1)))("month", date(2024, 2, 1), date(2024, 3, 1)),
        )
        self.assertEqual(
            previous_month(date(2025, 1, 1)),
            type(previous_month(date(2025, 1, 1)))("month", date(2024, 12, 1), date(2025, 1, 1)),
        )
        self.assertEqual(previous_week(date(2025, 1, 6)).end, date(2025, 1, 6))

    def test_one_kind_failure_does_not_skip_other_kind(self):
        def fail_week(period):
            self.built.append(period)
            if period.kind == "week":
                raise RuntimeError("provider details must stay private")
            return "monthly"

        scheduler = self.scheduler(build=fail_week)
        with self.assertRaises(ScheduledReportError) as raised:
            scheduler.run(datetime(2025, 9, 1, 9, tzinfo=timezone.utc))
        self.assertEqual(raised.exception.failed_kinds, ("week",))
        self.assertNotIn("provider details", str(raised.exception))
        self.assertEqual(len(self.sender.messages), 1)
        self.assertEqual(self.built[-1].kind, "month")

    def test_uncertain_kind_is_reported_without_resending_or_skipping_other_kind(self):
        class UncertainSender:
            def send_message(self, chat_id: str, text: str) -> int:
                raise DeliveryUncertain("unknown provider result")

        uncertain_delivery = DeliveryService(self.delivery.store, UncertainSender(), "test-chat")
        with self.assertRaises(DeliveryUncertain):
            uncertain_delivery.deliver(
                "week:2025-08-25:2025-09-01", "weekly", datetime(2025, 9, 1, 9, tzinfo=timezone.utc)
            )

        scheduler = self.scheduler()
        with self.assertRaises(ScheduledReportError) as raised:
            scheduler.run(datetime(2025, 9, 1, 10, tzinfo=timezone.utc))
        self.assertEqual(raised.exception.failed_kinds, ("week",))
        self.assertEqual([period.kind for period in self.built], ["month"])
        self.assertEqual(len(self.sender.messages), 1)


if __name__ == "__main__":
    unittest.main()
