import unittest
from datetime import datetime
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from dontanello.modules.calendar_planning.adapters.groq import GroqPlanningInterpreter
from dontanello.modules.calendar_planning.application import CalendarPlanningApplication
from dontanello.modules.calendar_planning.models import CalendarEvent


class MemoryCalendar:
    def __init__(self):
        self.items = []
        self.created = {}
        self.reads = 0

    def events(self, start, end):
        self.reads += 1
        return tuple(event for event in self.items if event.start < end and event.end > start)

    def create_event(self, event_id, proposal_id, title, slot, timezone):
        self.created[event_id] = proposal_id
        self.items.append(CalendarEvent(event_id, title, slot.start, slot.end))
        return event_id

    def delete_created_event(self, event_id, proposal_id):
        if self.created.get(event_id) != proposal_id:
            return False
        self.items = [event for event in self.items if event.id != event_id]
        del self.created[event_id]
        return True


class MemoryRepository:
    def __init__(self):
        self.items = {}
        self.updates = {}
        self.pending = None

    def proposal_for_update(self, update_id):
        proposal_id = self.updates.get(update_id)
        return self.items.get(proposal_id) if proposal_id else None

    def get_proposal(self, proposal_id):
        return self.items.get(proposal_id)

    def save_proposal(self, proposal):
        self.items[proposal.id] = proposal
        self.updates[proposal.update_id] = proposal.id

    def pending_intent(self):
        return self.pending

    def save_pending_intent(self, intent):
        self.pending = intent

    def clear_pending_intent(self):
        self.pending = None

    def save_proposal_and_clear_pending(self, proposal):
        self.save_proposal(proposal)
        self.clear_pending_intent()


class CalendarPlanningTests(unittest.TestCase):
    def setUp(self):
        self.zone = ZoneInfo("Asia/Bishkek")
        self.now = datetime(2026, 10, 4, 18, 0, tzinfo=self.zone)
        self.calendar = MemoryCalendar()
        self.repository = MemoryRepository()
        self.application = CalendarPlanningApplication(
            self.repository,
            self.calendar,
            self.zone,
            buffer_minutes=15,
            timezone_name=self.zone.key,
        )

    def test_deterministic_russian_request_suggests_without_creating(self):
        text = "сегодня хочу 1.5 часа позаниматься безопасностью"
        self.assertTrue(self.application.accepts_message(text, self.now))
        response = self.application.handle_message(1, text, self.now)
        self.assertIn("Безопасностью — 1 ч 30 мин", response.text)
        self.assertIn("18:00–19:30", response.text)
        self.assertEqual(self.calendar.created, {})
        self.assertEqual(len(response.button_rows), 2)

    def test_normalized_undated_focus_request_plans_for_today(self):
        text = "Сегодня хочу позаниматься безопасностью 1,5 часа"

        response = self.application.handle_message(75, text, self.now)

        self.assertIn("Безопасностью — 1 ч 30 мин", response.text)
        self.assertIn("сегодня", response.text)
        self.assertEqual(self.calendar.created, {})

    def test_request_without_duration_asks_and_remembers_context_until_answer(self):
        text = "Хочу завтра позаниматься информационной безопасностью"

        question = self.application.handle_message(70, text, self.now)

        self.assertIn("На сколько времени", question.text)
        self.assertEqual(self.calendar.reads, 0)
        self.assertEqual(self.calendar.created, {})
        self.assertTrue(self.application.accepts_message("1,5 часа", self.now))

        proposal = self.application.handle_message(71, "1,5 часа", self.now)

        self.assertIn("Информационной безопасностью — 1 ч 30 мин", proposal.text)
        self.assertIn("завтра, 05.10", proposal.text)
        self.assertEqual(len(self.repository.items), 1)
        self.assertIsNone(self.repository.pending_intent())

    def test_numeric_date_without_duration_is_calendar_request_not_task(self):
        text = "Хочу позаниматься информационной безопасностью в 09.10"

        question = self.application.handle_message(73, text, self.now)

        self.assertIn("09.10.2026", question.text)
        self.assertIn("На сколько времени", question.text)
        self.assertEqual(self.calendar.reads, 0)
        self.assertEqual(self.calendar.created, {})

        proposal = self.application.handle_message(74, "1,5 часа", self.now)

        self.assertIn("09.10", proposal.text)
        self.assertIn("1 ч 30 мин", proposal.text)
        self.assertEqual(len(self.repository.items), 1)

    def test_invalid_duration_answer_keeps_the_pending_request(self):
        self.application.handle_message(
            72, "Хочу завтра позаниматься информационной безопасностью", self.now
        )

        self.assertFalse(self.application.accepts_message("скоро", self.now))
        self.assertIsNotNone(self.repository.pending_intent())

    def test_confirmation_rereads_calendar_then_creates_and_undoes_only_own_event(self):
        response = self.application.handle_message(2, "сегодня 1 час почитать", self.now)
        add_button = response.button_rows[0][0]
        before_confirmation_reads = self.calendar.reads
        created = self.application.handle_callback(add_button.callback_data, self.now)
        self.assertEqual(self.calendar.reads, before_confirmation_reads + 1)
        self.assertIn("✅ Добавил", created.text)
        self.assertEqual(len(self.calendar.created), 1)
        self.assertTrue(created.button_rows)
        undone = self.application.handle_callback(created.button_rows[0][0].callback_data, self.now)
        self.assertIn("удалено", undone.text)
        self.assertEqual(self.calendar.created, {})

    def test_new_calendar_conflict_blocks_creation_and_returns_alternatives(self):
        proposal = self.application.handle_message(3, "сегодня 19:00-20:00 чтение", self.now)
        # A meeting appeared after the initial proposal.
        self.calendar.items.append(
            CalendarEvent(
                "meeting",
                "Встреча",
                datetime(2026, 10, 4, 18, 50, tzinfo=self.zone),
                datetime(2026, 10, 4, 19, 10, tzinfo=self.zone),
            )
        )
        callback = proposal.button_rows[0][0].callback_data
        response = self.application.handle_callback(callback, self.now)
        self.assertIn("пересекается", response.text)
        self.assertIn("Встреча", response.text)
        self.assertEqual(self.calendar.created, {})

    def test_more_options_are_limited_to_three(self):
        proposal = self.application.handle_message(4, "завтра 30 минут читать", self.now)
        more = proposal.button_rows[0][1]
        response = self.application.handle_callback(more.callback_data, self.now)
        self.assertLessEqual(len(response.button_rows), 4)
        self.assertLessEqual(response.text.count("• "), 3)

    def test_availability_shows_remaining_free_periods_and_day_navigation(self):
        from datetime import date

        self.calendar.items.append(
            CalendarEvent(
                "meeting",
                "Встреча",
                datetime(2026, 10, 4, 19, 0, tzinfo=self.zone),
                datetime(2026, 10, 4, 20, 0, tzinfo=self.zone),
            )
        )

        response = self.application.show_availability(date(2026, 10, 4), self.now)

        self.assertIn("18:00–18:45", response.text)
        self.assertIn("20:15–22:00", response.text)
        self.assertEqual(response.button_rows[0][0].label, "‹ 03.10")
        self.assertEqual(response.button_rows[0][1].label, "05.10 ›")
        self.assertEqual(self.calendar.created, {})

    def test_availability_callback_reads_the_selected_day(self):
        response = self.application.handle_callback("a:2026-10-05", self.now)

        self.assertIn("завтра", response.text)
        self.assertIn("08:00–22:00", response.text)

    def test_request_retry_does_not_create_a_second_proposal(self):
        first = self.application.handle_message(5, "завтра 1 час читать", self.now)
        second = self.application.handle_message(5, "завтра 1 час читать", self.now)
        self.assertEqual(first, second)
        self.assertEqual(len(self.repository.items), 1)

    def test_complex_request_fallback_returns_a_validated_structured_request(self):
        client = Mock()
        client.complete.return_value = (
            '{"date":"2026-10-10","title":"Подготовка к экзамену",'
            '"duration_minutes":90,"start_time":null,"end_time":null}'
        )
        interpreter = GroqPlanningInterpreter(client)
        self.application.interpreter = interpreter
        text = "в выходные выдели время на подготовку к экзамену"
        self.assertTrue(self.application.accepts_message(text, self.now))
        response = self.application.handle_message(6, text, self.now)
        self.assertIn("Подготовка к экзамену", response.text)
        client.complete.assert_called_once()
        self.assertEqual(client.complete.call_args.kwargs["max_output_tokens"], 250)

    def test_simple_request_never_calls_the_groq_fallback(self):
        client = Mock()
        self.application.interpreter = GroqPlanningInterpreter(client)
        response = self.application.handle_message(7, "завтра 1 час читать", self.now)
        self.assertIn("Читать", response.text)
        client.complete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
