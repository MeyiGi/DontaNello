"""Worker ownership and durable recovery across CLI invocations."""

import json
import os
import select
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from zoneinfo import ZoneInfo

from dontanello.bootstrap import build_runtime
from dontanello.modules.inbox.adapters.sqlite import SQLiteInboxCaptureStore
from dontanello.modules.task_capture.adapters.sqlite import SQLiteTaskCaptureRepository
from dontanello.modules.task_capture.models import TaskDraft, TaskProposal
from dontanello.platform.locking import worker_lock
from dontanello.platform.settings import Settings


class WorkerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = Settings(
            root=self.root,
            notion_token="test-token",
            telegram_token="",
            timezone=ZoneInfo("Asia/Bishkek"),
            poll_seconds=1,
            config={
                "completion_sources": [
                    {
                        "id": "completion-source",
                        "name": "Tasks",
                        "checkbox": "Done",
                        "completed_on": "Completed on",
                    }
                ],
                "inbox": {"data_source_id": "inbox-source", "title_property": "Name"},
                "reminders": {
                    "notion_tasks": {
                        "source_id": "tasks-source",
                        "title_property": "Name",
                        "due_property": "Due",
                        "next_due_property": "Next Due",
                        "completed_property": "Completed",
                        "status_property": "List",
                        "context_property": "Context",
                    }
                },
                "calendar_planning": {"calendar_id": "primary"},
            },
        )
        (self.root / "config").mkdir()
        (self.root / "config" / "settings.json").write_text(json.dumps(self.settings.config))
        (self.root / ".env").write_text("NOTION_TOKEN=test-token\n")

    def seed_interrupted_writes(self):
        inbox = SQLiteInboxCaptureStore(self.root / "state" / "inbox.sqlite3")
        inbox.claim(101, "Inbox idea", "2026-10-11T09:00:00+06:00")
        tasks = SQLiteTaskCaptureRepository(self.root / "state" / "task_capture.sqlite3")
        tasks.save_proposal(TaskProposal("task-102", 102, "Task", TaskDraft("Task"), "pending"))
        self.assertTrue(tasks.claim_create("task-102"))

    def write_statuses(self):
        # Inspect persisted outcomes without replaying claim(), which changes Inbox state.
        with closing(sqlite3.connect(self.root / "state" / "inbox.sqlite3")) as database:
            inbox_status = database.execute(
                "SELECT status FROM captures WHERE update_id=101"
            ).fetchone()[0]
        tasks = SQLiteTaskCaptureRepository(self.root / "state" / "task_capture.sqlite3")
        return inbox_status, tasks.proposal_for_update(102).status

    def test_building_runtime_does_not_recover_pending_writes(self):
        self.seed_interrupted_writes()

        build_runtime(self.settings)

        self.assertEqual(self.write_statuses(), ("inflight", "creating"))

    def cli_environment(self):
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("NOTION_", "TELEGRAM_", "GROQ_", "GOOGLE_", "WEATHER_"))
        }
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
        return environment

    def run_cli(self, *arguments, fail_reads=False):
        # The real CLI, settings loader, file lock and SQLite adapters run in a second process.
        # Only external Notion reads are replaced; writes are rejected by the fake.
        script = """
import os
from unittest.mock import patch
from dontanello.entrypoints.cli import main
schema = {"properties": {name: {"type": kind} for name, kind in {
    "Done": "checkbox", "Completed on": "date", "Name": "title", "Due": "date",
    "Next Due": "formula", "Completed": "formula", "List": "status", "Context": "multi_select"
}.items()}}
def request(self, method, path, *args, **kwargs):
    if method != "GET":
        raise AssertionError("Unexpected external write")
    if os.environ.get("DONTANELLO_TEST_FAIL_READS"):
        raise RuntimeError("Notion unavailable")
    return schema
with patch("dontanello.integrations.notion.client.NotionClient.request", request), \
     patch("dontanello.integrations.notion.client.NotionClient.list_all", return_value=[]):
    main()
"""
        environment = self.cli_environment()
        if fail_reads:
            environment["DONTANELLO_TEST_FAIL_READS"] = "1"
        return subprocess.run(
            [sys.executable, "-c", script, "--root", str(self.root), *arguments],
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
        )

    def test_check_does_not_create_local_state(self):
        result = self.run_cli("--check")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Notion: Tasks OK", result.stdout)
        self.assertFalse((self.root / "state").exists())

    def test_failed_check_does_not_create_local_state(self):
        result = self.run_cli("--check", fail_reads=True)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Notion unavailable", result.stderr)
        self.assertFalse((self.root / "state").exists())

    def test_rejected_concurrent_worker_does_not_initialize_state(self):
        self.seed_interrupted_writes()
        with worker_lock(self.root / "state" / "worker.lock"):
            files_before = set((self.root / "state").iterdir())

            result = self.run_cli("--once")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Другой экземпляр Dontanello уже работает", result.stderr)
            self.assertEqual(set((self.root / "state").iterdir()), files_before)
            self.assertEqual(self.write_statuses(), ("inflight", "creating"))

    def test_worker_recovers_interrupted_writes_and_repeat_start_preserves_outcomes(self):
        self.seed_interrupted_writes()
        (self.root / "state" / "checkboxes.json").write_text("{}")
        inbox = SQLiteInboxCaptureStore(self.root / "state" / "inbox.sqlite3")
        inbox.claim(103, "Saved idea", "2026-10-11T09:00:00+06:00")
        inbox.finish(103, "created", "https://example.com/inbox-103")
        tasks = SQLiteTaskCaptureRepository(self.root / "state" / "task_capture.sqlite3")
        tasks.save_proposal(
            TaskProposal(
                "task-104",
                104,
                "Saved task",
                TaskDraft("Saved task"),
                "created",
                "https://example.com/task-104",
            )
        )

        for _ in range(2):
            result = self.run_cli("--once")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.write_statuses(), ("uncertain", "uncertain"))
            saved = inbox.claim(103, "Saved idea", "2026-10-11T09:00:00+06:00")
            self.assertEqual(
                (saved.status, saved.page_url), ("created", "https://example.com/inbox-103")
            )
            saved_task = tasks.proposal_for_update(104)
            self.assertEqual(
                (saved_task.status, saved_task.page_url),
                ("created", "https://example.com/task-104"),
            )

    def test_check_preserves_existing_state_while_worker_lock_is_held(self):
        self.seed_interrupted_writes()
        inbox_path = self.root / "state" / "inbox.sqlite3"
        inbox_path.chmod(0o640)
        with worker_lock(self.root / "state" / "worker.lock"):
            before = {
                path.name: (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns)
                for path in (self.root / "state").iterdir()
            }

            result = self.run_cli("--check")

            self.assertEqual(result.returncode, 0, result.stderr)
            after = {
                path.name: (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns)
                for path in (self.root / "state").iterdir()
            }
            self.assertEqual(after, before)
        self.assertEqual(self.write_statuses(), ("inflight", "creating"))

    def test_killed_owner_releases_lock_and_next_worker_recovers(self):
        script = """
import signal
import sys
from pathlib import Path
from dontanello.bootstrap import build_runtime
from dontanello.modules.task_capture.models import TaskDraft, TaskProposal
from dontanello.platform.locking import worker_lock
from dontanello.platform.settings import load_settings
root = Path(sys.argv[1])
with worker_lock(root / "state" / "worker.lock"):
    (root / "state" / "checkboxes.json").write_text("{}")
    runtime = build_runtime(load_settings(root))
    runtime.inbox_app.store.claim(101, "Inbox idea", "2026-10-11T09:00:00+06:00")
    tasks = runtime.task_capture_app.repository
    tasks.save_proposal(TaskProposal("task-102", 102, "Task", TaskDraft("Task"), "pending"))
    tasks.claim_create("task-102")
    print("ready", flush=True)
    signal.pause()
"""
        owner = subprocess.Popen(
            [sys.executable, "-c", script, str(self.root)],
            env=self.cli_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertTrue(select.select([owner.stdout], [], [], 10)[0], "Owner did not start")
            self.assertEqual(owner.stdout.readline().strip(), "ready")
            self.assertEqual(self.write_statuses(), ("inflight", "creating"))
            owner.kill()
            owner.wait(timeout=5)

            result = self.run_cli("--once")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.write_statuses(), ("uncertain", "uncertain"))
        finally:
            if owner.poll() is None:
                owner.kill()
            owner.communicate(timeout=5)
