import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dontanello.modules.task_capture import TaskCaptureApplication
from dontanello.modules.task_capture.adapters.notion import (
    NotionTaskWriter,
    NotionTaskWriterConfig,
)
from dontanello.modules.task_capture.adapters.sqlite import SQLiteTaskCaptureRepository
from dontanello.modules.task_capture.models import TaskDraft, TaskWriteRejected
from dontanello.modules.task_capture.parser import parse_task_draft


class FakeWriter:
    def __init__(self):
        self.drafts = []
        self.error = None

    def create(self, draft):
        if self.error:
            raise self.error
        self.drafts.append(draft)
        return f"https://notion.test/{len(self.drafts)}"


class FakeNotion:
    def __init__(self):
        self.calls = []

    def request(self, method, path, payload, retry_server_errors=True):
        self.calls.append((method, path, payload, retry_server_errors))
        return {"url": "https://notion.test/task"}


class TaskCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "task_capture.sqlite3"
        self.repository = SQLiteTaskCaptureRepository(self.path)
        self.writer = FakeWriter()
        self.app = TaskCaptureApplication(self.repository, self.writer)
        self.now = datetime(2026, 10, 4, 12, 0, tzinfo=ZoneInfo("Asia/Bishkek"))

    def test_explicit_request_parses_title_and_tomorrow_deadline(self):
        draft = parse_task_draft("Добавь задачу прочитать презентацию до завтра", self.now.date())
        self.assertEqual(draft.title, "прочитать презентацию")
        self.assertEqual(draft.due_date, date(2026, 10, 5))
        self.assertIsNone(parse_task_draft("Просто прочитать презентацию", self.now.date()))

    def test_request_requires_confirmation_then_creates_once_with_link(self):
        prompt = self.app.handle_message(
            11, "Добавь задачу прочитать презентацию до завтра", self.now
        )
        self.assertIn("прочитать презентацию", prompt.text)
        self.assertIn("05.10.2026", prompt.text)
        self.assertIn("Backlog", prompt.text)
        self.assertEqual(self.writer.drafts, [])

        response = self.app.handle_callback(prompt.button_rows[0][0].callback_data)
        replay = self.app.handle_callback(prompt.button_rows[0][0].callback_data)
        self.assertIn("https://notion.test/1", response.text)
        self.assertEqual(response, replay)
        self.assertEqual(len(self.writer.drafts), 1)

    def test_cancel_does_not_write_and_unspecified_due_stays_empty(self):
        prompt = self.app.handle_message(12, "Создай задачу повторить тему", self.now)
        self.assertIn("Срок: не указан", prompt.text)
        response = self.app.handle_callback(prompt.button_rows[0][1].callback_data)
        self.assertIn("отменено", response.text)
        self.assertEqual(self.writer.drafts, [])

    def test_proposal_survives_restart_and_database_is_private(self):
        prompt = self.app.handle_message(13, "Добавь задачу проверить тему", self.now)
        restarted = TaskCaptureApplication(SQLiteTaskCaptureRepository(self.path), self.writer)
        restarted.recover_inflight()
        result = restarted.handle_callback(prompt.button_rows[0][0].callback_data)
        self.assertIn("notion.test/1", result.text)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_uncertain_create_is_never_retried(self):
        prompt = self.app.handle_message(14, "Добавь задачу проверить тему", self.now)
        callback = prompt.button_rows[0][0].callback_data
        self.writer.error = TimeoutError("acknowledgement lost")

        first = self.app.handle_callback(callback)
        self.writer.error = None
        replay = self.app.handle_callback(callback)

        self.assertIn("Проверь Tasks", first.text)
        self.assertEqual(first, replay)
        self.assertEqual(self.writer.drafts, [])

    def test_explicit_notion_rejection_is_recorded(self):
        prompt = self.app.handle_message(15, "Добавь задачу проверить тему", self.now)
        proposal_id = prompt.button_rows[0][0].callback_data.split(":")[1]
        self.writer.error = TaskWriteRejected()

        result = self.app.handle_callback(prompt.button_rows[0][0].callback_data)

        self.assertIn("Notion отклонил", result.text)
        self.assertEqual(self.repository.get_proposal(proposal_id).status, "rejected")

    def test_notion_adapter_uses_personal_tasks_schema(self):
        notion = FakeNotion()
        writer = NotionTaskWriter(
            notion,
            NotionTaskWriterConfig(
                "tasks-source", title_property="Name", due_property="Due", status_property="List"
            ),
        )
        result = writer.create(TaskDraft("прочитать презентацию", date(2026, 10, 5)))

        self.assertEqual(result, "https://notion.test/task")
        method, path, payload, retry = notion.calls[0]
        self.assertEqual((method, path, retry), ("POST", "pages", False))
        self.assertEqual(payload["parent"]["data_source_id"], "tasks-source")
        self.assertEqual(payload["properties"]["Due"]["date"]["start"], "2026-10-05")
        self.assertEqual(payload["properties"]["List"]["status"]["name"], "Backlog 🐛")


if __name__ == "__main__":
    unittest.main()
