"""Notion task deadline translation tests with a fake API boundary."""

import unittest
from datetime import date
from zoneinfo import ZoneInfo

from dontanello.modules.reminders.adapters.notion import (
    NotionTaskConfig,
    NotionTaskDeadlineSource,
)


class FakeNotion:
    def __init__(self, pages):
        self.pages = pages

    def list_all(self, method, path, body):
        return iter(self.pages)

    def request(self, method, path):
        return {
            "properties": {
                "Name": {"type": "title"},
                "Due": {"type": "date"},
                "Next Due": {"type": "formula"},
                "Completed": {"type": "formula"},
                "Сделано": {"type": "checkbox"},
                "Готово": {"type": "checkbox"},
                "List": {"type": "status"},
            }
        }


def page(identifier, due=None, next_due=None, done=False, status="To do", archived=False):
    return {
        "id": identifier,
        "url": f"https://notion.test/{identifier}",
        "archived": archived,
        "properties": {
            "Name": {"title": [{"plain_text": identifier}]},
            "Due": {"date": {"start": due} if due else None},
            "Next Due": {"formula": {"date": {"start": next_due} if next_due else None}},
            "Completed": {"formula": {"type": "boolean", "boolean": done}},
            "Сделано": {"checkbox": False},
            "Готово": {"checkbox": False},
            "List": {"status": {"name": status}},
        },
    }


class NotionReminderAdapterTests(unittest.TestCase):
    def test_reads_due_fallback_and_excludes_done_cancelled_archived_and_undated(self):
        source = NotionTaskDeadlineSource(
            FakeNotion(
                [
                    page("due", due="2026-10-05"),
                    page("recurring", next_due="2026-10-06"),
                    page("formula-done", due="2026-10-05", done=True),
                    page("cancelled", due="2026-10-05", status="Cancel ❌"),
                    page("archived", due="2026-10-05", archived=True),
                    page("undated"),
                ]
            ),
            NotionTaskConfig("task-source"),
            ZoneInfo("Asia/Bishkek"),
        )

        source.validate()
        tasks = source.tasks()

        self.assertEqual(
            [task.id for task in tasks],
            ["due", "recurring", "formula-done", "cancelled"],
        )
        self.assertEqual(
            [task.due_date for task in tasks[:2]], [date(2026, 10, 5), date(2026, 10, 6)]
        )
        self.assertEqual(tasks[0].url, "https://notion.test/due")
        self.assertTrue(tasks[2].completed)
        self.assertTrue(tasks[3].cancelled)


if __name__ == "__main__":
    unittest.main()
