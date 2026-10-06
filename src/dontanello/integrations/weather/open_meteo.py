"""Small standard-library client for Open-Meteo's public APIs."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from typing import Any


class OpenMeteoError(RuntimeError):
    """The public forecast service did not return usable data."""


class OpenMeteoClient:
    def __init__(self, timeout: float = 12.0) -> None:
        if timeout <= 0:
            raise ValueError("Open-Meteo timeout must be positive")
        self.timeout = timeout

    def geocode(self, city: str) -> dict[str, Any] | None:
        payload = self._get(
            "https://geocoding-api.open-meteo.com/v1/search",
            {"name": city, "count": "1", "language": "ru", "format": "json"},
        )
        results = payload.get("results", [])
        if not isinstance(results, list) or not results:
            return None
        result = results[0]
        return result if isinstance(result, dict) else None

    def forecast(
        self, latitude: float, longitude: float, day: date, timezone_name: str
    ) -> dict[str, Any]:
        return self._get(
            "https://api.open-meteo.com/v1/forecast",
            {
                "latitude": str(latitude),
                "longitude": str(longitude),
                "current": ("temperature_2m,apparent_temperature,weather_code,wind_speed_10m"),
                "hourly": "temperature_2m,weather_code",
                "daily": (
                    "weather_code,temperature_2m_min,temperature_2m_max,"
                    "precipitation_probability_max,precipitation_sum,wind_speed_10m_max,sunrise,sunset"
                ),
                "start_date": day.isoformat(),
                "end_date": day.isoformat(),
                "timezone": timezone_name,
                "wind_speed_unit": "kmh",
            },
        )

    def _get(self, endpoint: str, params: dict[str, str]) -> dict[str, Any]:
        url = endpoint + "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={"User-Agent": "Dontanello/0.1"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            raise OpenMeteoError(f"Open-Meteo HTTP {error.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise OpenMeteoError("Open-Meteo request failed") from None
        if not isinstance(payload, dict) or payload.get("error") is True:
            raise OpenMeteoError("Open-Meteo returned invalid data")
        return payload
