import unittest
from datetime import date

from dontanello.modules.reports.compaction import compact_observations, project_timelines
from dontanello.modules.reports.progress_models import Evidence


def evidence(identifier: str, text: str, minute: int) -> Evidence:
    return Evidence(
        id=identifier,
        source_id="page-" + identifier,
        project="REP-14039",
        occurred_on=date(2026, 9, 21),
        recorded_at=f"2026-09-21T09:{minute:02}:00+06:00",
        text=text,
        source_kind="work",
    )


class EventCompactionTests(unittest.TestCase):
    def test_collapses_format_only_repeated_observation_and_keeps_source_ids(self):
        events = compact_observations(
            [
                evidence("one", "guest flow works", 0),
                evidence("two", "guest   flow works.", 5),
                evidence("three", "guest flow works!", 10),
            ]
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].source_ids, ("one", "two", "three"))
        self.assertEqual(events[0].observation_count, 3)
        self.assertEqual(events[0].recorded_at, "2026-09-21T09:10:00+06:00")
        self.assertEqual(events[0].event_type, "work_observation")

    def test_ignores_snapshot_date_suffix_instead_of_creating_fake_progress(self):
        first = evidence("one", "REP-14039 — 21.09.2026 09:00", 0)
        later = evidence("two", "REP-14039 — 21.09.2026 09:05", 5)

        events = compact_observations((first, later))

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].source_ids, ("one", "two"))

    def test_keeps_state_transitions_negation_and_version_identifiers(self):
        events = compact_observations(
            [
                evidence("one", "queue is blocked, v1.2", 0),
                evidence("two", "queue is available, v1.2", 5),
                evidence("three", "queue is available, v12", 10),
            ]
        )

        self.assertEqual(len(events), 3)
        self.assertEqual(events[0].text, "queue is blocked, v1.2")

    def test_does_not_merge_repeated_work_far_apart_in_time(self):
        first = evidence("morning", "Reviewed the same report", 0)
        later = evidence("afternoon", "Reviewed the same report", 30)

        events = compact_observations((first, later))

        self.assertEqual(len(events), 2)

    def test_builds_project_timeline_in_date_order(self):
        events = compact_observations(
            [evidence("later", "implementation complete", 15), evidence("first", "started", 0)]
        )

        timelines = project_timelines(events)

        self.assertEqual(len(timelines), 1)
        self.assertEqual(timelines[0].start_event_id, events[0].id)
        self.assertEqual(timelines[0].end_event_id, events[1].id)


if __name__ == "__main__":
    unittest.main()
