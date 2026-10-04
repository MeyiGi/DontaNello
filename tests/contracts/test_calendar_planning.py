import stat
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dontanello.integrations.google_calendar.client import GoogleCalendarClient
from dontanello.modules.calendar_planning.adapters.google_calendar import GoogleCalendarAdapter
from dontanello.modules.calendar_planning.adapters.sqlite import SQLitePlanningRepository
from dontanello.modules.calendar_planning.models import PendingPlanIntent, PlanProposal, TimeSlot


class CalendarPlanningAdapterTests(unittest.TestCase):
    def test_repository_persists_proposal_and_has_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state" / "planning.sqlite3"
            proposal = PlanProposal(
                "proposal",
                55,
                "tomorrow 1 hour read",
                "Read",
                date(2026, 10, 5),
                60,
                (
                    TimeSlot(
                        datetime(2026, 10, 5, 10, tzinfo=ZoneInfo("Asia/Bishkek")),
                        datetime(2026, 10, 5, 11, tzinfo=ZoneInfo("Asia/Bishkek")),
                    ),
                ),
                0,
                "pending",
            )
            SQLitePlanningRepository(path).save_proposal(proposal)
            restarted = SQLitePlanningRepository(path)
            self.assertEqual(restarted.proposal_for_update(55), proposal)
            self.assertEqual(restarted.get_proposal("proposal"), proposal)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_pending_duration_intent_survives_restart_and_clears_with_proposal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state" / "planning.sqlite3"
            zone = ZoneInfo("Asia/Bishkek")
            intent = PendingPlanIntent(
                70,
                "Хочу завтра позаниматься информационной безопасностью",
                "Информационной безопасностью",
                date(2026, 10, 5),
                datetime(2026, 10, 4, 18, tzinfo=zone),
            )
            repository = SQLitePlanningRepository(path)
            repository.save_pending_intent(intent)
            restarted = SQLitePlanningRepository(path)
            self.assertEqual(restarted.pending_intent(), intent)

            proposal = PlanProposal(
                "proposal-after-duration",
                71,
                "Хочу завтра позаниматься информационной безопасностью — 90 минут",
                intent.title,
                intent.day,
                90,
                (
                    TimeSlot(
                        datetime(2026, 10, 5, 10, tzinfo=zone),
                        datetime(2026, 10, 5, 11, 30, tzinfo=zone),
                    ),
                ),
                0,
                "pending",
            )
            restarted.save_proposal_and_clear_pending(proposal)
            final = SQLitePlanningRepository(path)
            self.assertEqual(final.proposal_for_update(71), proposal)
            self.assertIsNone(final.pending_intent())

    def test_google_adapter_normalizes_events_and_ignores_free_or_cancelled_events(self):
        class Client:
            def events(self, *_):
                return (
                    {
                        "id": "busy",
                        "summary": "Focus",
                        "start": {"dateTime": "2026-10-04T10:00:00+06:00"},
                        "end": {"dateTime": "2026-10-04T11:00:00+06:00"},
                    },
                    {
                        "id": "free",
                        "transparency": "transparent",
                        "start": {"dateTime": "2026-10-04T11:00:00+06:00"},
                        "end": {"dateTime": "2026-10-04T12:00:00+06:00"},
                    },
                    {
                        "id": "cancelled",
                        "status": "cancelled",
                        "start": {"dateTime": "2026-10-04T12:00:00+06:00"},
                        "end": {"dateTime": "2026-10-04T13:00:00+06:00"},
                    },
                    {
                        "id": "all-day",
                        "summary": "Holiday",
                        "start": {"date": "2026-10-04"},
                        "end": {"date": "2026-10-05"},
                    },
                )

        zone = ZoneInfo("Asia/Bishkek")
        result = GoogleCalendarAdapter(Client(), zone).events(
            datetime(2026, 10, 4, 9, tzinfo=zone), datetime(2026, 10, 4, 20, tzinfo=zone)
        )
        self.assertEqual([event.id for event in result], ["busy", "all-day"])
        self.assertEqual(result[0].start.hour, 10)
        self.assertEqual(result[1].end.date(), date(2026, 10, 5))

    def test_google_insert_uses_timezone_and_private_proposal_identity(self):
        client = GoogleCalendarClient(Path("oauth.json"), Path("token.json"))
        calls = []

        def request(method, path, params=None, body=None):
            calls.append((method, path, body))
            return {"id": "dn-proposal"}

        client._request = request
        result = client.insert_event(
            "dn-proposal",
            "proposal",
            "Study",
            "2026-10-04T19:00:00+06:00",
            "2026-10-04T20:30:00+06:00",
            "Asia/Bishkek",
        )
        self.assertEqual(result, "dn-proposal")
        method, path, body = calls[0]
        self.assertEqual((method, path), ("POST", "/calendars/primary/events"))
        self.assertEqual(body["start"]["timeZone"], "Asia/Bishkek")
        self.assertEqual(body["extendedProperties"]["private"]["dontanelloProposalId"], "proposal")

    def test_google_delete_refuses_an_event_without_matching_private_proposal_identity(self):
        client = GoogleCalendarClient(Path("oauth.json"), Path("token.json"))
        calls = []

        def request(method, path, params=None, body=None):
            calls.append(method)
            return {
                "id": "event",
                "extendedProperties": {"private": {"dontanelloProposalId": "other"}},
            }

        client._request = request
        self.assertFalse(client.delete_created_event("event", "this-proposal"))
        self.assertEqual(calls, ["GET"])


if __name__ == "__main__":
    unittest.main()
