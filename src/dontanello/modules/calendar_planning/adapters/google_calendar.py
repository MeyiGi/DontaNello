"""Map Google Calendar events to the planner's internal calendar contract."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from dontanello.integrations.google_calendar.client import (
    GoogleCalendarAuthorizationError,
    GoogleCalendarClient,
    GoogleCalendarRequestError,
)

from ..models import (
    CalendarEvent,
    CalendarNotConnected,
    CalendarUnavailable,
    TimeSlot,
)


class GoogleCalendarAdapter:
    def __init__(self, client: GoogleCalendarClient, timezone: ZoneInfo):
        self.client = client
        self.timezone = timezone

    def events(self, start: datetime, end: datetime) -> tuple[CalendarEvent, ...]:
        try:
            rows = self.client.events(start.isoformat(), end.isoformat(), self.timezone.key)
        except GoogleCalendarAuthorizationError:
            raise CalendarNotConnected from None
        except GoogleCalendarRequestError:
            raise CalendarUnavailable from None
        events = []
        for row in rows:
            if row.get("status") == "cancelled" or row.get("transparency") == "transparent":
                continue
            event_id = row.get("id")
            raw_start = row.get("start")
            raw_end = row.get("end")
            if (
                not isinstance(event_id, str)
                or not isinstance(raw_start, dict)
                or not isinstance(raw_end, dict)
            ):
                raise CalendarUnavailable("Google Calendar returned an invalid event")
            try:
                event_start = _parse_boundary(raw_start, self.timezone)
                event_end = _parse_boundary(raw_end, self.timezone)
            except (KeyError, ValueError, TypeError):
                raise CalendarUnavailable(
                    "Google Calendar returned an invalid event time"
                ) from None
            if event_start < end and event_end > start:
                events.append(
                    CalendarEvent(
                        event_id, str(row.get("summary") or "Занято"), event_start, event_end
                    )
                )
        return tuple(events)

    def create_event(
        self, event_id: str, proposal_id: str, title: str, slot: TimeSlot, timezone: str
    ) -> str:
        try:
            return self.client.insert_event(
                event_id,
                proposal_id,
                title,
                slot.start.isoformat(),
                slot.end.isoformat(),
                timezone,
            )
        except GoogleCalendarAuthorizationError:
            raise CalendarNotConnected from None
        except GoogleCalendarRequestError:
            raise CalendarUnavailable from None

    def delete_created_event(self, event_id: str, proposal_id: str) -> bool:
        try:
            return self.client.delete_created_event(event_id, proposal_id)
        except GoogleCalendarAuthorizationError:
            raise CalendarNotConnected from None
        except GoogleCalendarRequestError:
            raise CalendarUnavailable from None


def _parse_boundary(value: dict[str, object], timezone: ZoneInfo) -> datetime:
    date_time = value.get("dateTime")
    if isinstance(date_time, str):
        result = datetime.fromisoformat(date_time.replace("Z", "+00:00"))
        if result.tzinfo is None:
            return result.replace(tzinfo=timezone)
        return result.astimezone(timezone)
    day = value.get("date")
    if isinstance(day, str):
        return datetime.combine(date.fromisoformat(day), time.min, timezone)
    raise ValueError("event boundary has no time")
