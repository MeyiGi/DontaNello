"""Optional Groq fallback for planning requests the deterministic parser misses."""

from __future__ import annotations

import json
from datetime import datetime, time

from dontanello.integrations.groq.client import GroqClient

from ..models import PlanRequest, TimeSlot

_SYSTEM = """Extract a personal calendar planning request from the user's Russian text.
Return only a JSON object with keys date, title, duration_minutes, start_time, end_time.
Use YYYY-MM-DD for date and HH:MM for times. Use null for unknown fields.
Infer date only from explicit wording or the supplied local current date. Do not invent a title.
For a time range, return both start_time and end_time. Otherwise return duration_minutes.
Do not answer the user or propose a calendar slot."""


class GroqPlanningInterpreter:
    def __init__(self, client: GroqClient):
        self.client = client

    def interpret(self, text: str, now: datetime) -> PlanRequest | None:
        prompt = f"Local date and time: {now.isoformat()}\nUser request: {text}"
        response = self.client.complete(_SYSTEM, prompt, max_output_tokens=250)
        try:
            value = json.loads(response)
            if not isinstance(value, dict):
                return None
            day_raw = value.get("date")
            title = value.get("title")
            if not isinstance(day_raw, str) or not isinstance(title, str):
                return None
            day = datetime.fromisoformat(day_raw).date()
            title = " ".join(title.split())[:120]
            if not title or day < now.date():
                return None
            start_raw, end_raw = value.get("start_time"), value.get("end_time")
            if start_raw is not None or end_raw is not None:
                if not isinstance(start_raw, str) or not isinstance(end_raw, str):
                    return None
                start = time.fromisoformat(start_raw)
                end = time.fromisoformat(end_raw)
                if start.second or start.microsecond or end.second or end.microsecond:
                    return None
                start_dt = datetime.combine(day, start, now.tzinfo)
                end_dt = datetime.combine(day, end, now.tzinfo)
                minutes = int((end_dt - start_dt).total_seconds() // 60)
                if not 15 <= minutes <= 480 or end_dt <= start_dt:
                    return None
                return PlanRequest(day, title, minutes, TimeSlot(start_dt, end_dt))
            minutes_raw = value.get("duration_minutes")
            if type(minutes_raw) is not int or not 15 <= minutes_raw <= 480:
                return None
            return PlanRequest(day, title, minutes_raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
