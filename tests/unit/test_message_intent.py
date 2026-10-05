import json
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from dontanello.modules.message_intent import MessageIntentUnavailable
from dontanello.modules.message_intent.adapters.groq import GroqMessageIntentInterpreter


class FakeGroq:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def complete(self, system, user, *, max_output_tokens=None, reasoning_effort="medium"):
        self.calls.append((system, json.loads(user), max_output_tokens, reasoning_effort))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class MessageIntentTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 4, 19, 0, tzinfo=ZoneInfo("Asia/Bishkek"))

    def test_interprets_polished_calendar_request_without_inventing_duration(self):
        client = FakeGroq(
            json.dumps(
                {
                    "destination": "calendar",
                    "confidence": "high",
                    "title": "Информационная безопасность",
                    "due_date": None,
                    "normalized_text": "Хочу позаниматься информационной безопасностью 09.10",
                },
                ensure_ascii=False,
            )
        )

        intent = GroqMessageIntentInterpreter(client).interpret(
            "Хочу позаниматься информационной безосностью в 09.10",
            self.now,
            inbox_prompt_pending=True,
        )

        self.assertEqual(intent.destination, "calendar")
        self.assertEqual(
            intent.normalized_text, "Хочу позаниматься информационной безопасностью 09.10"
        )
        self.assertTrue(client.calls[0][1]["inbox_prompt_pending"])
        self.assertEqual(client.calls[0][2:], (900, "low"))

    def test_undated_focus_request_defaults_to_today_for_calendar(self):
        client = FakeGroq(
            json.dumps(
                {
                    "destination": "calendar",
                    "confidence": "high",
                    "title": "Безопасность",
                    "due_date": None,
                    "normalized_text": "Сегодня хочу позаниматься безопасностью 1,5 часа",
                },
                ensure_ascii=False,
            )
        )

        intent = GroqMessageIntentInterpreter(client).interpret(
            "Хочу позанматсья безопасностью 1.5 часа",
            self.now,
            inbox_prompt_pending=False,
        )

        self.assertEqual(intent.destination, "calendar")
        self.assertIn("treat it as today", client.calls[0][0])
        self.assertIn("План хочу позаниматься безопасностью 1.5 часа", client.calls[0][0])
        self.assertIn("инженерной экономикой час", client.calls[0][0])
        self.assertIn("xfcf", client.calls[0][0])
        self.assertIn("литкод", client.calls[0][0])
        self.assertEqual(client.calls[0][1]["local_datetime"], self.now.isoformat())
        self.assertIn("сегодня", intent.normalized_text.casefold())

    def test_parses_explicit_task_due_date_even_if_already_overdue(self):
        client = FakeGroq(
            json.dumps(
                {
                    "destination": "task",
                    "confidence": "medium",
                    "title": "Проверить FTP у Райымбека агая",
                    "due_date": "2026-10-02",
                    "normalized_text": "",
                },
                ensure_ascii=False,
            )
        )

        intent = GroqMessageIntentInterpreter(client).interpret(
            "Добавь задачу проверить FTP у Райымбек агая в пятницу",
            self.now,
            inbox_prompt_pending=False,
        )

        self.assertEqual(intent.due_date, date(2026, 10, 2))

    def test_normalizes_reminder_prefix_for_existing_application(self):
        client = FakeGroq(
            json.dumps(
                {
                    "destination": "reminder",
                    "confidence": "high",
                    "title": "",
                    "due_date": None,
                    "normalized_text": "завтра вечером позвонить Райымбеку",
                },
                ensure_ascii=False,
            )
        )

        intent = GroqMessageIntentInterpreter(client).interpret(
            "Завтра вечером позвони мне Райымбек агаю",
            self.now,
            inbox_prompt_pending=False,
        )

        self.assertEqual(intent.normalized_text, "Напомни завтра вечером позвонить Райымбеку")

    def test_rejects_invalid_or_low_quality_model_output(self):
        invalid = FakeGroq("not json")
        with self.assertRaises(MessageIntentUnavailable):
            GroqMessageIntentInterpreter(invalid).interpret(
                "что-то", self.now, inbox_prompt_pending=False
            )

        malformed = FakeGroq(
            json.dumps(
                {
                    "destination": [],
                    "confidence": "high",
                    "title": "x",
                    "normalized_text": "",
                }
            )
        )
        with self.assertRaises(MessageIntentUnavailable):
            GroqMessageIntentInterpreter(malformed).interpret(
                "что-то", self.now, inbox_prompt_pending=False
            )

        unavailable = FakeGroq(RuntimeError("unavailable"))
        with self.assertRaises(MessageIntentUnavailable):
            GroqMessageIntentInterpreter(unavailable).interpret(
                "что-то", self.now, inbox_prompt_pending=False
            )


if __name__ == "__main__":
    unittest.main()
