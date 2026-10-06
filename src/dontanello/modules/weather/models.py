"""Internal weather values and failure types."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class HourlyForecast:
    at: datetime
    temperature_c: float
    weather_code: int


@dataclass(frozen=True)
class DailyForecast:
    location: str
    current_temperature_c: float
    apparent_temperature_c: float
    current_weather_code: int
    minimum_temperature_c: float
    maximum_temperature_c: float
    precipitation_probability: int | None
    precipitation_mm: float
    maximum_wind_kmh: float
    sunrise: datetime
    sunset: datetime
    hourly: tuple[HourlyForecast, ...]


@dataclass(frozen=True)
class DailyDelivery:
    status: str
    text: str | None
    next_attempt: datetime | None


class WeatherUnavailable(RuntimeError):
    """Forecast could not be retrieved or validated."""


class WeatherSendRejected(RuntimeError):
    """Telegram explicitly rejected the weather message."""


class WeatherSendUncertain(RuntimeError):
    """Telegram may have accepted the weather message."""
