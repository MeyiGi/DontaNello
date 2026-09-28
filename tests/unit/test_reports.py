import unittest
from datetime import date

from dontanello.modules.reports import (
    Period,
    ReportItem,
    build_report,
    previous_month,
    previous_week,
)
from dontanello.modules.reports.application import render_report


class ReportPeriodTests(unittest.TestCase):
    def test_previous_week_is_full_monday_to_monday_across_year_boundary(self):
        self.assertEqual(
            previous_week(date(2026, 1, 1)),
            Period("week", date(2025, 12, 22), date(2025, 12, 29)),
        )

    def test_previous_month_handles_leap_year_and_year_boundary(self):
        self.assertEqual(
            previous_month(date(2024, 3, 15)),
            Period("month", date(2024, 2, 1), date(2024, 3, 1)),
        )
        self.assertEqual(
            previous_month(date(2025, 1, 1)),
            Period("month", date(2024, 12, 1), date(2025, 1, 1)),
        )


class ReportApplicationTests(unittest.TestCase):
    def test_build_report_deduplicates_by_id_and_section_and_sorts(self):
        first = ReportItem("1", "Поздняя задача", date(2026, 2, 2), "url/1", "tasks")
        duplicate = ReportItem("1", "Повтор", date(2026, 2, 2), "url/1", "tasks")
        same_id_other_section = ReportItem("1", "Цель", date(2026, 2, 1), "url/goal", "goals")

        class Source:
            def __init__(self, items):
                self.values = items

            def items(self, period):
                self.period = period
                return self.values

        period = Period("month", date(2026, 2, 1), date(2026, 3, 1))
        rendered = build_report(
            period,
            [Source([first, same_id_other_section]), Source([duplicate])],
        )

        self.assertIn("Всего записей: 2", rendered)
        self.assertIn("Цель — 01.02.2026", rendered)
        self.assertIn("Поздняя задача — 02.02.2026", rendered)
        self.assertNotIn("Повтор", rendered)
        self.assertLess(rendered.index("Задачи"), rendered.index("Цели"))
        self.assertIn("Работа — активность (0):", rendered)

    def test_empty_report_explains_each_empty_section(self):
        report = render_report(Period("week", date(2026, 5, 4), date(2026, 5, 11)), [])
        self.assertIn("Всего записей: 0", report)
        self.assertEqual(report.count("За этот период записей нет."), 3)

    def test_small_report_is_unchanged_in_compact_mode(self):
        period = Period("week", date(2026, 5, 4), date(2026, 5, 11))
        items = [ReportItem("a", "Одна задача", date(2026, 5, 5), "url/a", "tasks")]
        self.assertEqual(render_report(period, items), render_report(period, items, compact=False))

    def test_compact_digest_counts_all_items_and_full_mode_keeps_every_item(self):
        period = Period("week", date(2026, 5, 4), date(2026, 5, 11))
        items = [
            ReportItem(
                str(day),
                f"Задача {day}",
                date(2026, 5, day),
                f"url/{day}",
                "tasks",
                "d" * 320,
            )
            for day in range(1, 12)
        ]

        compact = render_report(period, items)
        full = render_report(period, items, compact=False)

        class Source:
            def items(self, requested_period):
                self.period = requested_period
                return items

        self.assertIn("Всего записей: 11", compact)
        self.assertIn("Задачи (11):", compact)
        self.assertIn("Показаны последние 10 из 11; полный список: /week full", compact)
        self.assertNotIn("Задача 1 —", compact)
        self.assertIn("Задача 11 —", compact)
        self.assertIn("url/11", compact)
        self.assertNotIn("Показаны последние", full)
        self.assertIn("Задача 1 —", full)
        self.assertIn("Задача 11 —", full)
        self.assertIn("d" * 320, full)
        self.assertEqual(build_report(period, [Source()], compact=False), full)

    def test_compact_omission_uses_month_full_command_for_monthly_reports(self):
        period = Period("month", date(2026, 1, 1), date(2026, 2, 1))
        items = [
            ReportItem(str(index), str(index), date(2026, 1, index), "", "goals")
            for index in range(1, 12)
        ]
        self.assertIn(
            "Показаны последние 10 из 11; полный список: /month full", render_report(period, items)
        )

    def test_compact_truncates_titles_and_details(self):
        period = Period("month", date(2026, 1, 1), date(2026, 2, 1))
        item = ReportItem(
            "long",
            "T" * 205,
            date(2026, 1, 5),
            "url/long",
            "goals",
            "D" * 305,
        )

        compact = render_report(period, [item])
        full = render_report(period, [item], compact=False)

        self.assertIn("T" * 199 + "…", compact)
        self.assertIn("D" * 299 + "…", compact)
        self.assertNotIn("T" * 205, compact)
        self.assertIn("T" * 205, full)
        self.assertIn("D" * 305, full)


if __name__ == "__main__":
    unittest.main()
