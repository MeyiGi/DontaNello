"""Translate Open-Meteo responses into the weather capability's values."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from dontanello.integrations.weather.open_meteo import OpenMeteoError

from ..models import DailyForecast, HourlyForecast, WeatherUnavailable


class OpenMeteoGateway(Protocol):
    def geocode(self, city: str) -> dict[str, Any] | None: ...
    def forecast(
        self, latitude: float, longitude: float, day: date, timezone_name: str
    ) -> dict[str, Any]: ...


class OpenMeteoWeatherProvider:
    def __init__(self, client: OpenMeteoGateway) -> None:
        self.client = client
        self._locations: dict[str, tuple[str, float, float]] = {}

    def forecast(self, city: str, day: date, timezone_name: str) -> DailyForecast:
        try:
            location = self._location(city)
            raw = self.client.forecast(location[1], location[2], day, timezone_name)
            return _map_forecast(location[0], raw, ZoneInfo(timezone_name))
        except (OpenMeteoError, KeyError, TypeError, ValueError, IndexError):
            raise WeatherUnavailable("Open-Meteo forecast is unavailable") from None

    def _location(self, city: str) -> tuple[str, float, float]:
        key = city.casefold().strip()
        if key in self._locations:
            return self._locations[key]
        result = self.client.geocode(city)
        if result is None:
            raise WeatherUnavailable("Weather location was not found")
        name = result.get("name")
        country = result.get("country")
        latitude = _number(result.get("latitude"))
        longitude = _number(result.get("longitude"))
        if not isinstance(name, str) or not name or latitude is None or longitude is None:
            raise WeatherUnavailable("Weather location response is invalid")
        label = f"{name}, {country}" if isinstance(country, str) and country else name
        location = (label, latitude, longitude)
        self._locations[key] = location
        return location


def _map_forecast(location: str, raw: dict[str, Any], timezone: ZoneInfo) -> DailyForecast:
    current = raw["current"]
    daily = raw["daily"]
    hourly = raw["hourly"]
    if not isinstance(current, dict) or not isinstance(daily, dict) or not isinstance(hourly, dict):
        raise ValueError
    hourly_times = hourly["time"]
    temperatures = hourly["temperature_2m"]
    codes = hourly["weather_code"]
    if not all(isinstance(values, list) for values in (hourly_times, temperatures, codes)):
        raise ValueError
    if not len(hourly_times) == len(temperatures) == len(codes):
        raise ValueError
    hourly_values = tuple(
        HourlyForecast(
            _datetime(at, timezone),
            _required_number(temperature),
            _required_int(code),
        )
        for at, temperature, code in zip(hourly_times, temperatures, codes, strict=True)
    )
    probability = _daily_value(daily, "precipitation_probability_max", optional=True)
    return DailyForecast(
        location=location,
        current_temperature_c=_required_number(current["temperature_2m"]),
        apparent_temperature_c=_required_number(current["apparent_temperature"]),
        current_weather_code=_required_int(current["weather_code"]),
        minimum_temperature_c=_required_number(_daily_value(daily, "temperature_2m_min")),
        maximum_temperature_c=_required_number(_daily_value(daily, "temperature_2m_max")),
        precipitation_probability=None if probability is None else int(probability),
        precipitation_mm=_required_number(_daily_value(daily, "precipitation_sum")),
        maximum_wind_kmh=_required_number(_daily_value(daily, "wind_speed_10m_max")),
        sunrise=_datetime(_daily_value(daily, "sunrise"), timezone),
        sunset=_datetime(_daily_value(daily, "sunset"), timezone),
        hourly=hourly_values,
    )


def _daily_value(values: dict[str, Any], key: str, *, optional: bool = False) -> Any:
    items = values.get(key)
    if not isinstance(items, list) or not items:
        if optional:
            return None
        raise ValueError
    return items[0]


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _required_number(value: Any) -> float:
    result = _number(value)
    if result is None:
        raise ValueError
    return result


def _required_int(value: Any) -> int:
    number = _number(value)
    if number is None or not number.is_integer():
        raise ValueError
    return int(number)


def _datetime(value: Any, timezone: ZoneInfo) -> datetime:
    if not isinstance(value, str):
        raise ValueError
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=timezone) if parsed.tzinfo is None else parsed.astimezone(timezone)
