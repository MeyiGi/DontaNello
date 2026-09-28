from datetime import date, datetime
from zoneinfo import ZoneInfo


class LocalClock:
    def __init__(self, timezone: ZoneInfo):
        self.timezone = timezone

    def today(self) -> date:
        return datetime.now(self.timezone).date()
