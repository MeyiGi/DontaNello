import json
import sys
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from hypothesis import given
from hypothesis import strategies as st

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

    @given(day=st.dates(), minutes=st.integers(min_value=1, max_value=480))
    def test_calendar_json_preserves_date_and_integer_duration(self, day, minutes):
        client = FakeGroq(
            json.dumps(
                {
                    "destination": "calendar",
                    "confidence": "medium",
                    "title": "Занятие",
                    "calendar": {"date": day.isoformat(), "duration_minutes": minutes},
                }
            )
        )
        intent = GroqMessageIntentInterpreter(client).interpret(
            "исходный текст", self.now, inbox_prompt_pending=False
        )
        self.assertEqual(intent.calendar.day, day)
        self.assertEqual(intent.calendar.duration_minutes, minutes)
        self.assertEqual(len(client.calls), 1)

    @given(minutes=st.one_of(st.booleans(), st.text(), st.floats(allow_nan=False)))
    def test_calendar_duration_rejects_non_integer_json_types(self, minutes):
        client = FakeGroq(
            json.dumps(
                {
                    "destination": "calendar",
                    "confidence": "high",
                    "title": "Занятие",
                    "calendar": {"date": "2026-10-05", "duration_minutes": minutes},
                }
            )
        )
        with self.assertRaises(MessageIntentUnavailable):
            GroqMessageIntentInterpreter(client).interpret(
                "текст", self.now, inbox_prompt_pending=False
            )

    def test_irrelevant_nested_fields_are_ignored_without_enabling_actions(self):
        for destination, confidence in (("calendar", "low"), ("inbox", "high")):
            client = FakeGroq(
                json.dumps(
                    {
                        "destination": destination,
                        "confidence": confidence,
                        "title": "Идея",
                        "calendar": {"date": [], "duration_minutes": True},
                        "reminder": [],
                    }
                )
            )
            intent = GroqMessageIntentInterpreter(client).interpret(
                "текст", self.now, inbox_prompt_pending=False
            )
            self.assertIsNone(intent.calendar)
            self.assertIsNone(intent.reminder)

    def test_nonstandard_numbers_in_unused_fields_preserve_legacy_json_behavior(self):
        client = FakeGroq(
            json.dumps(
                {
                    "destination": "inbox",
                    "confidence": "high",
                    "title": "Идея",
                    "calendar": {"duration_minutes": float("nan")},
                }
            )
        )
        intent = GroqMessageIntentInterpreter(client).interpret(
            "текст", self.now, inbox_prompt_pending=False
        )
        self.assertEqual(intent.destination, "inbox")
        self.assertIsNone(intent.calendar)

    def test_legacy_basic_iso_clock_is_preserved(self):
        client = FakeGroq(
            json.dumps(
                {
                    "destination": "calendar",
                    "confidence": "high",
                    "title": "Занятие",
                    "calendar": {
                        "date": "2026-10-05",
                        "start_time": "T1900",
                        "end_time": "T2000",
                    },
                }
            )
        )
        interpreter = GroqMessageIntentInterpreter(client)
        # Basic ISO clocks were added to time.fromisoformat in Python 3.11.
        # Preserve each supported Python version's existing acceptance rules.
        if sys.version_info < (3, 11):
            with self.assertRaises(MessageIntentUnavailable):
                interpreter.interpret("текст", self.now, inbox_prompt_pending=False)
            return
        intent = interpreter.interpret("текст", self.now, inbox_prompt_pending=False)
        self.assertEqual(intent.calendar.start_time.isoformat(), "19:00:00")
        self.assertEqual(intent.calendar.end_time.isoformat(), "20:00:00")

    def test_calendar_fields_are_extracted_without_reparsing_normalized_text(self):
        client = FakeGroq(
            json.dumps(
                {
                    "destination": "calendar",
                    "confidence": "high",
                    "title": "Безопасность",
                    "normalized_text": "Исправленный текст без синтаксиса планировщика",
                    "calendar": {
                        "date": "2026-10-05",
                        "duration_minutes": 90,
                        "start_time": None,
                        "end_time": None,
                    },
                }
            )
        )

        intent = GroqMessageIntentInterpreter(client).interpret(
            "Завтра безопасность полтора часа", self.now, inbox_prompt_pending=False
        )

        self.assertEqual(intent.calendar.day, date(2026, 10, 5))
        self.assertEqual(intent.calendar.duration_minutes, 90)
        self.assertEqual(len(client.calls), 1)

    def test_interprets_polished_calendar_request_without_inventing_duration(self):
        client = FakeGroq(
            json.dumps(
                {
                    "destination": "calendar",
                    "confidence": "high",
                    "title": "Информационная безопасность",
                    "due_date": None,
                    "normalized_text": "Хочу позаниматься информационной безопасностью 09.10",
                    "calendar": {"date": "2026-10-09", "duration_minutes": None},
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
                    "calendar": {"date": "2026-10-04", "duration_minutes": 90},
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

    def test_extracts_reminder_schedule_without_inventing_daypart_clock(self):
        client = FakeGroq(
            json.dumps(
                {
                    "destination": "reminder",
                    "confidence": "high",
                    "title": "Позвонить Райымбеку",
                    "due_date": None,
                    "normalized_text": "завтра вечером позвонить Райымбеку",
                    "reminder": {"date": "2026-10-05", "daypart": "evening"},
                },
                ensure_ascii=False,
            )
        )

        intent = GroqMessageIntentInterpreter(client).interpret(
            "Завтра вечером позвони мне Райымбек агаю",
            self.now,
            inbox_prompt_pending=False,
        )

        self.assertEqual(intent.title, "Позвонить Райымбеку")
        self.assertEqual(intent.reminder.day, date(2026, 10, 5))
        self.assertEqual(intent.reminder.daypart, "evening")
        self.assertIsNone(intent.reminder.clock)

    def test_rejects_malformed_structured_schedules_without_text_fallback(self):
        cases = [
            {"destination": "calendar", "calendar": None},
            {"destination": "calendar", "calendar": {"date": "bad", "duration_minutes": 60}},
            {
                "destination": "calendar",
                "calendar": {"date": "2026-10-05", "duration_minutes": True},
            },
            {"destination": "calendar", "calendar": {"date": "2026-10-05", "start_time": "19:00"}},
            {"destination": "reminder", "reminder": {"daypart": []}},
            {"destination": "reminder", "reminder": {"time": "25:00"}},
            {"destination": "reminder", "reminder": {"delay_minutes": -2}},
            {"destination": "reminder", "reminder": {"date": "2026-10-05", "delay_minutes": 60}},
        ]
        for fields in cases:
            with self.subTest(fields=fields):
                client = FakeGroq(
                    json.dumps(
                        {
                            "confidence": "high",
                            "title": "Занятие",
                            "normalized_text": "завтра 1 час на диплом",
                            **fields,
                        }
                    )
                )
                with self.assertRaises(MessageIntentUnavailable):
                    GroqMessageIntentInterpreter(client).interpret(
                        "запрос", self.now, inbox_prompt_pending=False
                    )

    def test_low_confidence_calendar_does_not_require_actionable_schedule(self):
        client = FakeGroq(json.dumps({"destination": "calendar", "confidence": "low"}))
        intent = GroqMessageIntentInterpreter(client).interpret(
            "может потом", self.now, inbox_prompt_pending=False
        )
        self.assertEqual(intent.confidence, "low")
        self.assertIsNone(intent.calendar)

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
