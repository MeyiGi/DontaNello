import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from dontanello.modules.completion import CompletionTracker
from dontanello.modules.completion.adapters.json_state import JsonCheckboxState
from dontanello.modules.completion.adapters.notion import (
    NotionCompletionConfig,
    NotionCompletionSource,
)
from tests.unit.test_completion import FakeSource, FixedClock


class FakeClient:
    def __init__(self):
        self.writes = []

    def list_all(self, *args):
        return [
            {"id": "page", "properties": {"Готово": {"checkbox": True}}},
            {"id": "archived", "archived": True},
            {"id": "trashed", "in_trash": True},
        ]

    def request(self, method, path, body=None):
        if method == "GET":
            return {"properties": {"Готово": {"checkbox": True}}}
        self.writes.append((path, body))


class NotionAdapterTests(unittest.TestCase):
    def test_preserves_original_state_key_and_date_payload(self):
        client = FakeClient()
        adapter = NotionCompletionSource(
            client, NotionCompletionConfig("source", "Tasks", "Готово", "Completed On")
        )
        observations = list(adapter.observations())
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0].key, "source:Готово:page")
        self.assertTrue(adapter.is_checked("page"))
        adapter.stamp("page", date(2026, 9, 28))
        self.assertEqual(
            client.writes,
            [("pages/page", {"properties": {"Completed On": {"date": {"start": "2026-09-28"}}}})],
        )


class JsonStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "checkboxes.json"

    def test_old_state_survives_restart_without_duplicate_completion(self):
        self.path.write_text(json.dumps({"source:Done:page": False}))
        source = FakeSource()
        source.checked = True
        tracker = CompletionTracker(source, JsonCheckboxState(self.path), FixedClock())
        self.assertEqual(tracker.run(), 1)
        restarted = CompletionTracker(source, JsonCheckboxState(self.path), FixedClock())
        self.assertEqual(restarted.run(), 0)
        self.assertEqual(len(source.writes), 1)

    def test_corrupt_state_is_rejected_without_reset(self):
        self.path.write_text('{"key": "false"}')
        with self.assertRaises(ValueError):
            JsonCheckboxState(self.path)
        self.assertEqual(self.path.read_text(), '{"key": "false"}')

    def test_failed_replace_keeps_previous_memory_and_file(self):
        state = JsonCheckboxState(self.path)
        state.record("key", False)
        with patch.object(Path, "replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                state.record("key", True)
        self.assertFalse(state.get("key"))
        self.assertFalse(JsonCheckboxState(self.path).get("key"))
