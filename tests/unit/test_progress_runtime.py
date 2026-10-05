import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from dontanello.bootstrap import build_runtime
from dontanello.modules.reports import Period
from dontanello.platform.settings import Settings


class ProgressRuntimeTests(unittest.TestCase):
    def test_message_intent_uses_the_configured_groq_key_pool(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(
                root=Path(directory),
                notion_token="test-notion",
                telegram_token="",
                timezone=ZoneInfo("Asia/Bishkek"),
                poll_seconds=30,
                config={"completion_sources": [], "reports": {"sources": []}},
                groq_planning_api_key="planning-primary",
                groq_api_keys=("primary", "fallback"),
            )
            with patch("dontanello.bootstrap.GroqClient") as client_factory:
                runtime = build_runtime(settings)

        client_factory.assert_called_once_with(
            "planning-primary",
            "openai/gpt-oss-20b",
            timeout=15,
            max_output_tokens=350,
            api_keys=("primary", "fallback"),
        )
        self.assertIsNotNone(runtime.message_intent_interpreter)

    def test_cli_telegram_and_scheduler_share_scoped_progress_application(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(
                root=Path(directory),
                notion_token="test-notion",
                telegram_token="",
                timezone=ZoneInfo("Asia/Bishkek"),
                poll_seconds=30,
                config={"completion_sources": [], "reports": {"sources": []}},
                telegram_chat_id="123",
                groq_api_key="test-groq",
            )
            application = Mock()
            application.report.return_value = "structured progress review"
            with patch("dontanello.bootstrap.ProgressReports", return_value=application) as factory:
                runtime = build_runtime(settings)
            self.assertEqual(factory.call_args.args[3], "123")
            self.assertEqual(factory.call_args.kwargs["clock"]().tzinfo, settings.timezone)
            period = Period("week", date(2026, 9, 21), date(2026, 9, 28))
            self.assertEqual(runtime.report(period), "structured progress review")
            application.report.assert_called_once_with(period)
            # Explicit raw diagnostics bypass LLM analysis and the progress archive.
            runtime.report(period, full=True)
            application.report.assert_called_once()
