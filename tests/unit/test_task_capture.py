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
from dontanello.platform.settings import Settings


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
        colloquial = parse_task_draft(
            "Добавь задача проверить ftp у Райымбек агая в пятницу", self.now.date()
        )
        self.assertEqual(colloquial.title, "проверить ftp у Райымбек агая")
        self.assertEqual(colloquial.due_date, date(2026, 10, 9))
        self.assertIsNone(parse_task_draft("Просто прочитать презентацию", self.now.date()))

    def test_explicit_request_creates_immediately_and_replays_same_result(self):
        prompt = self.app.handle_message(
            11, "Добавь задачу прочитать презентацию до завтра", self.now
        )
        self.assertIn("прочитать презентацию", prompt.text)
        self.assertIn("05.10.2026", prompt.text)
        self.assertIn("✅ Добавил задачу", prompt.text)
        self.assertIn("https://notion.test/1", prompt.text)
        replay = self.app.handle_message(
            11, "Добавь задачу прочитать презентацию до завтра", self.now
        )
        self.assertEqual(prompt, replay)
        self.assertEqual(len(self.writer.drafts), 1)

    def test_unspecified_due_is_empty_and_explicit_task_is_written(self):
        prompt = self.app.handle_message(12, "Создай задачу повторить тему", self.now)
        self.assertIn("✅ Добавил задачу", prompt.text)
        self.assertEqual(self.writer.drafts[0].due_date, None)

    def test_interpreted_task_draft_is_idempotently_written(self):
        draft = TaskDraft("Проверить FTP у Райымбека агая", date(2026, 10, 9))

        first = self.app.handle_draft(33, "проверь ftp у Райымбека агая в пятницу", draft)
        replay = self.app.handle_draft(33, "проверь ftp у Райымбека агая в пятницу", draft)

        self.assertEqual(first, replay)
        self.assertIn("09.10.2026", first.text)
        self.assertEqual(self.writer.drafts, [draft])

    def test_proposal_survives_restart_and_database_is_private(self):
        self.app.handle_message(13, "Добавь задачу проверить тему", self.now)
        restarted = TaskCaptureApplication(SQLiteTaskCaptureRepository(self.path), self.writer)
        restarted.recover_inflight()
        proposal = self.repository.proposal_for_update(13)
        result = restarted.handle_callback(f"t:{proposal.id}:add")
        self.assertIn("notion.test/1", result.text)
        self.assertEqual(len(self.writer.drafts), 1)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_uncertain_create_is_never_retried(self):
        self.writer.error = TimeoutError("acknowledgement lost")

        first = self.app.handle_message(14, "Добавь задачу проверить тему", self.now)
        self.writer.error = None
        replay = self.app.handle_message(14, "Добавь задачу проверить тему", self.now)

        self.assertIn("Проверь Tasks", first.text)
        self.assertEqual(first, replay)
        self.assertEqual(self.writer.drafts, [])

    def test_explicit_notion_rejection_is_recorded(self):
        self.writer.error = TaskWriteRejected()

        result = self.app.handle_message(15, "Добавь задачу проверить тему", self.now)

        self.assertIn("Notion отклонил", result.text)
        proposal_id = self.repository.proposal_for_update(15).id
        self.assertEqual(self.repository.get_proposal(proposal_id).status, "rejected")

    def test_runtime_keeps_creation_status_out_of_deadline_adapter_config(self):
        from dontanello.bootstrap import build_runtime

        settings = Settings(
            root=Path(self.temp.name),
            notion_token="test-token",
            telegram_token="",
            timezone=ZoneInfo("Asia/Bishkek"),
            poll_seconds=1,
            config={
                "completion_sources": [],
                "reminders": {
                    "notion_tasks": {
                        "source_id": "tasks-source",
                        "title_property": "Name",
                        "due_property": "Due",
                        "next_due_property": "Next Due",
                        "checkbox_properties": ["Сделано", "Готово"],
                        "status_property": "List",
                        "context_property": "Context",
                        "create_default_status": "Backlog 🐛",
                    }
                },
            },
        )

        runtime = build_runtime(settings)

        self.assertIsNotNone(runtime.task_deadline_source)
        self.assertIsNotNone(runtime.task_capture_app)
        self.assertEqual(runtime.task_capture_app.writer.config.default_status, "Backlog 🐛")

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
