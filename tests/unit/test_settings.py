import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from dontanello.platform.clock import LocalClock
from dontanello.platform.settings import load_settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()
        self.source = {
            "id": "source",
            "name": "Tasks",
            "checkbox": "Done",
            "completed_on": "Completed",
        }
        (self.root / "config" / "settings.json").write_text(
            json.dumps({"completion_sources": [self.source]})
        )
        (self.root / ".env").write_text("NOTION_TOKEN=file-token\nTIMEZONE=Asia/Bishkek\n")

    def test_environment_overrides_file_without_global_mutation_or_secret_repr(self):
        with patch.dict("os.environ", {"NOTION_TOKEN": "environment-token"}, clear=True):
            settings = load_settings(self.root)
            self.assertEqual(settings.notion_token, "environment-token")
            self.assertNotIn("environment-token", repr(settings))

    def test_duplicate_checkbox_rejected(self):
        (self.root / "config" / "settings.json").write_text(
            json.dumps({"completion_sources": [self.source, self.source]})
        )
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(ValueError):
            load_settings(self.root)

    def test_invalid_schedule_rejected_at_startup(self):
        (self.root / "config" / "settings.json").write_text(
            json.dumps(
                {
                    "completion_sources": [self.source],
                    "reports": {"hour": 25},
                }
            )
        )
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(ValueError):
            load_settings(self.root)

    def test_clock_uses_bishkek_calendar_date(self):
        instant = datetime(2026, 9, 27, 20, 0, tzinfo=timezone.utc)
        with patch("dontanello.platform.clock.datetime") as clock:
            clock.now.side_effect = lambda tz: instant.astimezone(tz)
            self.assertEqual(LocalClock(ZoneInfo("Asia/Bishkek")).today().isoformat(), "2026-09-28")
