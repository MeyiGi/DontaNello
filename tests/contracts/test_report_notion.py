import unittest
from datetime import date
from zoneinfo import ZoneInfo

from dontanello.modules.reports import Period
from dontanello.modules.reports.adapters.notion import (
    NotionReportConfig,
    NotionReportSource,
)


class FakeNotionClient:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def list_all(self, method, path, body=None):
        self.calls.append((method, path, body))
        return iter(self.pages)


def page(identifier, *, date_value="2026-02-01", checked=True, name="Запись", **flags):
    return {
        "id": identifier,
        "url": f"https://notion.test/{identifier}",
        **flags,
        "properties": {
            "Date": {"date": {"start": date_value} if date_value else None},
            "Name": {"title": [{"plain_text": name}]},
            "Done": {"checkbox": checked},
            "Completed": {"formula": {"type": "boolean", "boolean": False}},
            "Notes": {"rich_text": [{"plain_text": "Подробности"}]},
        },
    }


class NotionReportSourceTests(unittest.TestCase):
    def test_tasks_filter_completed_and_recheck_inclusive_exclusive_boundaries(self):
        client = FakeNotionClient(
            [
                page("first", date_value="2026-02-01", checked=True),
                page("boundary", date_value="2026-01-31T22:30:00Z", checked=True),
                page("last", date_value="2026-02-28", checked=True),
                page("exclusive", date_value="2026-03-01", checked=True),
                page("unchecked", date_value="2026-02-03", checked=False),
                page("archive", archived=True),
                page("trash", in_trash=True),
                page("missing", date_value=None),
            ]
        )
        source = NotionReportSource(
            client,
            NotionReportConfig("source-id", "Tasks", "Date", "Name", ("Done",), ("Notes",)),
            ZoneInfo("Asia/Bishkek"),
        )

        result = list(source.items(Period("month", date(2026, 2, 1), date(2026, 3, 1))))

        self.assertEqual([item.id for item in result], ["first", "boundary", "last"])
        self.assertEqual(result[0].details, "Notes: Подробности")
        self.assertEqual(
            client.calls[0],
            (
                "POST",
                "data_sources/source-id/query",
                {
                    "filter": {
                        "and": [
                            {
                                "property": "Date",
                                "date": {"on_or_after": "2026-01-31"},
                            },
                            {
                                "property": "Date",
                                "date": {"before": "2026-03-02"},
                            },
                        ]
                    }
                },
            ),
        )

    def test_task_formula_completion_and_timezone_conversion(self):
        item = page("formula", date_value="2026-03-01T00:30:00+03:00", checked=False)
        item["properties"]["Completed"]["formula"]["boolean"] = True
        source = NotionReportSource(
            FakeNotionClient([item]),
            NotionReportConfig("id", "Tasks", "Date", "Name", (), completed_property="Completed"),
            ZoneInfo("Asia/Bishkek"),
        )
        result = list(source.items(Period("month", date(2026, 2, 1), date(2026, 3, 1))))
        self.assertEqual(result, [])  # Timestamp is March 1 in the configured timezone.

        item["properties"]["Date"]["date"]["start"] = "2026-02-28T23:30:00+08:00"
        result = list(source.items(Period("month", date(2026, 2, 1), date(2026, 3, 1))))
        self.assertEqual([value.completed_on for value in result], [date(2026, 2, 28)])

    def test_goals_require_checkbox_but_work_log_is_dated_activity(self):
        pages = [page("unchecked", checked=False, date_value="2026-02-05")]
        period = Period("month", date(2026, 2, 1), date(2026, 3, 1))
        goals = NotionReportSource(
            FakeNotionClient(pages),
            NotionReportConfig("id", "Goals", "Date", "Name", ("Done",)),
            ZoneInfo("UTC"),
        )
        work = NotionReportSource(
            FakeNotionClient(pages),
            NotionReportConfig("id", "Work", "Date", "Name", ("Done",)),
            ZoneInfo("UTC"),
        )
        self.assertEqual(list(goals.items(period)), [])
        work_item = list(work.items(period))[0]
        self.assertEqual(work_item.section, "work")
        self.assertEqual(work_item.completed_on, date(2026, 2, 5))

    def test_cancelled_task_is_excluded_even_when_checkbox_is_checked(self):
        cancelled = page("cancelled", checked=True)
        cancelled["properties"]["List"] = {"status": {"name": "Cancel ❌"}}
        source = NotionReportSource(
            FakeNotionClient([cancelled]),
            NotionReportConfig(
                "id",
                "Tasks",
                "Date",
                "Name",
                ("Done",),
                completed_property="Completed",
                excluded_status_property="List",
                excluded_status_values=("Cancel ❌",),
            ),
            ZoneInfo("UTC"),
        )
        self.assertEqual(
            list(source.items(Period("month", date(2026, 2, 1), date(2026, 3, 1)))),
            [],
        )


if __name__ == "__main__":
    unittest.main()
