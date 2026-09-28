import unittest
from datetime import date

from dontanello.modules.completion import CompletionTracker
from dontanello.modules.completion.models import Observation


class FixedClock:
    day = date(2026, 9, 27)

    def today(self):
        return self.day


class MemoryState:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def record(self, key, checked):
        self.values[key] = checked


class FakeSource:
    checked = False
    rechecked = None
    fail = False

    def __init__(self):
        self.writes = []

    def observations(self):
        return [Observation("source:Done:page", "page", self.checked)]

    def is_checked(self, item_id):
        return self.checked if self.rechecked is None else self.rechecked

    def stamp(self, item_id, completed_on):
        if self.fail:
            raise RuntimeError("network")
        self.writes.append((item_id, completed_on))


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.source = FakeSource()
        self.state = MemoryState()
        self.clock = FixedClock()
        self.tracker = CompletionTracker(self.source, self.state, self.clock)

    def test_existing_completion_only_initializes_baseline(self):
        self.source.checked = True
        self.assertEqual(self.tracker.run(), 0)
        self.assertEqual(self.source.writes, [])

    def test_completion_is_written_once_and_recompletion_updates_date(self):
        self.tracker.run()
        self.source.checked = True
        self.assertEqual(self.tracker.run(), 1)
        self.clock.day = date(2026, 9, 28)
        self.tracker.run()
        self.assertEqual(self.source.writes, [("page", date(2026, 9, 27))])
        self.source.checked = False
        self.tracker.run()
        self.assertEqual(len(self.source.writes), 1)
        self.source.checked = True
        self.tracker.run()
        self.assertEqual(self.source.writes[-1], ("page", date(2026, 9, 28)))

    def test_unchecked_during_poll_is_not_stamped(self):
        self.tracker.run()
        self.source.checked = True
        self.source.rechecked = False
        self.tracker.run()
        self.assertEqual(self.source.writes, [])

    def test_failed_write_is_retried_without_losing_transition(self):
        self.tracker.run()
        self.source.checked = True
        self.source.fail = True
        with self.assertRaises(RuntimeError):
            self.tracker.run()
        self.assertFalse(self.state.get("source:Done:page"))
        self.source.fail = False
        self.assertEqual(self.tracker.run(), 1)
