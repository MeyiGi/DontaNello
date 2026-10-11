import unittest
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from hypothesis import given
from hypothesis import strategies as st

from dontanello.modules.calendar_planning.application import CalendarPlanningApplication
from dontanello.modules.calendar_planning.models import CalendarEvent, PlanRequest


class MemoryCalendar:
    def __init__(self, events):
        self.items = events

    def events(self, start, end):
        return tuple(event for event in self.items if event.start < end and event.end > start)


class MemoryRepository:
    def __init__(self):
        self.proposals = {}

    def proposal_for_update(self, update_id):
        return self.proposals.get(update_id)

    def save_proposal(self, proposal):
        self.proposals[proposal.id] = proposal

    def pending_intent(self):
        return None


class CalendarIntervalProperties(unittest.TestCase):
    @given(
        events=st.lists(
            st.tuples(
                st.integers(min_value=8 * 60, max_value=21 * 60 + 59),
                st.integers(min_value=1, max_value=120),
            ),
            max_size=4,
        )
    )
    def test_suggested_slot_has_requested_duration_and_avoids_buffered_events(self, events):
        zone = ZoneInfo("Asia/Bishkek")
        now = datetime(2026, 10, 4, 18, 0, tzinfo=zone)
        day = date(2026, 10, 5)
        midnight = datetime.combine(day, time.min, tzinfo=zone)
        busy_events = tuple(
            CalendarEvent(
                str(index),
                "Busy",
                midnight + timedelta(minutes=start_minute),
                midnight + timedelta(minutes=start_minute + duration),
            )
            for index, (start_minute, duration) in enumerate(events)
        )
        repository = MemoryRepository()
        application = CalendarPlanningApplication(
            repository,
            MemoryCalendar(busy_events),
            zone,
            buffer_minutes=15,
            timezone_name=zone.key,
        )

        application.handle_request(1, "Study", PlanRequest(day, "Study", 30), now)

        proposal = next(iter(repository.proposals.values()))
        slot = proposal.options[proposal.selected_index]
        self.assertEqual(slot.end - slot.start, timedelta(minutes=30))
        self.assertGreaterEqual(slot.start, midnight + timedelta(hours=8))
        self.assertLessEqual(slot.end, midnight + timedelta(hours=22))
        for event in busy_events:
            buffered_start = event.start - timedelta(minutes=15)
            buffered_end = event.end + timedelta(minutes=15)
            self.assertFalse(slot.start < buffered_end and slot.end > buffered_start)


if __name__ == "__main__":
    unittest.main()
