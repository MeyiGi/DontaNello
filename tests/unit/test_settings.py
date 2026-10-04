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

    def test_invalid_weekly_weekday_rejected_at_startup(self):
        (self.root / "config" / "settings.json").write_text(
            json.dumps(
                {
                    "completion_sources": [self.source],
                    "reports": {"weekly_weekday": 7},
                }
            )
        )
        with (
            patch.dict("os.environ", {}, clear=True),
            self.assertRaisesRegex(ValueError, "weekly_weekday"),
        ):
            load_settings(self.root)

    def test_clock_uses_bishkek_calendar_date(self):
        instant = datetime(2026, 9, 27, 20, 0, tzinfo=timezone.utc)
        with patch("dontanello.platform.clock.datetime") as clock:
            clock.now.side_effect = lambda tz: instant.astimezone(tz)
            self.assertEqual(LocalClock(ZoneInfo("Asia/Bishkek")).today().isoformat(), "2026-09-28")

    def test_enabled_reports_require_groq_at_startup(self):
        config = {
            "completion_sources": [self.source],
            "reports": {
                "enabled": True,
                "sources": [
                    {
                        "id": "work",
                        "name": "Work",
                        "date_property": "Date",
                        "title_property": "Name",
                    }
                ],
            },
        }
        (self.root / "config" / "settings.json").write_text(json.dumps(config))
        with (
            patch.dict("os.environ", {}, clear=True),
            self.assertRaisesRegex(ValueError, "GROQ_API_KEY"),
        ):
            load_settings(self.root)
        with patch.dict("os.environ", {"GROQ_API_KEY": "private-groq-key"}, clear=True):
            settings = load_settings(self.root)
            self.assertNotIn("private-groq-key", repr(settings))

    def test_groq_key_pool_is_unique_ordered_and_never_in_settings_repr(self):
        with patch.dict(
            "os.environ",
            {
                "GROQ_API_KEY": "fixture-primary-key",
                "GROQ_API_KEYS": "fixture-secondary-key, fixture-primary-key, fixture-third-key",
            },
            clear=True,
        ):
            settings = load_settings(self.root)
        self.assertEqual(
            settings.groq_api_keys,
            (
                "fixture-primary-key",
                "fixture-secondary-key",
                "fixture-third-key",
            ),
        )
        for key in settings.groq_api_keys:
            self.assertNotIn(key, repr(settings))

    def test_groq_key_pool_can_supply_primary_and_is_bounded(self):
        with patch.dict("os.environ", {"GROQ_API_KEYS": "fixture-fallback-key"}, clear=True):
            self.assertEqual(load_settings(self.root).groq_api_key, "fixture-fallback-key")
        with patch.dict(
            "os.environ",
            {"GROQ_API_KEYS": ",".join(f"fixture-key-{i}" for i in range(11))},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "GROQ_API_KEYS"):
                load_settings(self.root)

    def test_progress_budgets_and_reasoning_are_validated(self):
        for name, value in (
            ("max_rounds", 4),
            ("max_batches", 49),
            ("max_requests", 97),
            ("max_batch_chars", 20001),
            ("max_input_chars", 0),
            ("max_output_tokens", 8001),
            ("monthly_reasoning", "max"),
            ("weekly_reasoning", "none"),
        ):
            with self.subTest(name=name):
                (self.root / "config" / "settings.json").write_text(
                    json.dumps(
                        {"completion_sources": [self.source], "reports": {"ai": {name: value}}}
                    )
                )
                with patch.dict("os.environ", {}, clear=True):
                    with self.assertRaisesRegex(ValueError, "reports.ai"):
                        load_settings(self.root)

    def test_empty_groq_model_is_rejected(self):
        with patch.dict(
            "os.environ", {"GROQ_API_KEY": "private-groq-key", "GROQ_MODEL": ""}, clear=True
        ):
            with self.assertRaisesRegex(ValueError, "GROQ_MODEL"):
                load_settings(self.root)

    def test_calendar_settings_validate_window_and_buffer_without_disabling_bot(self):
        config = {"completion_sources": [self.source], "calendar_planning": {}}
        (self.root / "config" / "settings.json").write_text(json.dumps(config))
        with patch.dict("os.environ", {}, clear=True):
            settings = load_settings(self.root)
        self.assertIsNone(settings.google_calendar_client_secret_file)
        config["calendar_planning"] = {"day_start": "23:00", "day_end": "22:00"}
        (self.root / "config" / "settings.json").write_text(json.dumps(config))
        with (
            patch.dict("os.environ", {}, clear=True),
            self.assertRaisesRegex(ValueError, "day_start"),
        ):
            load_settings(self.root)

    def test_planning_model_uses_its_own_key_and_does_not_change_report_model(self):
        with patch.dict(
            "os.environ",
            {
                "GROQ_API_KEY": "report-key",
                "GROQ_MODEL": "report-model",
                "GROQ_PLANNING_API_KEY": "planner-key",
                "GROQ_PLANNING_MODEL": "planner-model",
            },
            clear=True,
        ):
            settings = load_settings(self.root)
        self.assertEqual(settings.groq_model, "report-model")
        self.assertEqual(settings.groq_planning_model, "planner-model")
        for secret in ("report-key", "planner-key"):
            self.assertNotIn(secret, repr(settings))

    def test_personal_reports_cannot_be_configured_for_a_group(self):
        with patch.dict("os.environ", {"TELEGRAM_CHAT_ID": "-1001234567890"}, clear=True):
            with self.assertRaisesRegex(ValueError, "личного чата"):
                load_settings(self.root)

    def test_reminder_configuration_requires_valid_schedule_and_horizon(self):
        base = {
            "completion_sources": [self.source],
            "reminders": {
                "notion_tasks": {
                    "source_id": "tasks",
                    "title_property": "Name",
                    "due_property": "Due",
                    "next_due_property": "Next Due",
                },
            },
        }
        for key, value in (("time", "6:00"), ("days_ahead", 366), ("weekdays", [7])):
            with self.subTest(key=key):
                config = json.loads(json.dumps(base))
                config["reminders"][key] = value
                (self.root / "config" / "settings.json").write_text(json.dumps(config))
                with patch.dict("os.environ", {}, clear=True), self.assertRaises(ValueError):
                    load_settings(self.root)

    def test_inbox_configuration_requires_a_data_source_and_title_property(self):
        config = {"completion_sources": [self.source]}
        config["inbox"] = {"data_source_id": "inbox-source", "title_property": "Name"}
        (self.root / "config" / "settings.json").write_text(json.dumps(config))
        self.assertEqual(load_settings(self.root).config["inbox"], config["inbox"])
        config["inbox"] = {"data_source_id": ""}
        (self.root / "config" / "settings.json").write_text(json.dumps(config))
        with self.assertRaises(ValueError):
            load_settings(self.root)
