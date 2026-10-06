"""Weather summary and once-per-local-day Telegram delivery."""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, tzinfo

from .models import (
    DailyForecast,
    WeatherSendRejected,
    WeatherSendUncertain,
    WeatherUnavailable,
)
from .ports import WeatherForecastProvider, WeatherRepository, WeatherSender

_LOGGER = logging.getLogger(__name__)
_RETRY_DELAY = timedelta(minutes=5)
_CACHE_TTL = timedelta(minutes=10)

_WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
_MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def render_forecast(forecast: DailyForecast, day: date) -> str:
    icon, condition = _condition(forecast.current_weather_code)
    lines = [
        f"{icon} Погода · {forecast.location}",
        f"{_WEEKDAYS[day.weekday()].capitalize()}, {day.day} {_MONTHS[day.month - 1]}",
        "",
        f"Сейчас: {_temperature(forecast.current_temperature_c)} · {condition}, "
        f"ощущается {_temperature(forecast.apparent_temperature_c)}",
        f"За день: {_temperature(forecast.minimum_temperature_c)} … "
        f"{_temperature(forecast.maximum_temperature_c)}",
    ]
    if forecast.precipitation_probability is None:
        lines.append(f"Осадки: {forecast.precipitation_mm:g} мм")
    else:
        lines.append(
            f"🌧 Осадки: до {forecast.precipitation_probability}% · "
            f"{_decimal(forecast.precipitation_mm)} мм"
        )
    lines.extend(
        [
            f"💨 Ветер: до {forecast.maximum_wind_kmh:.0f} км/ч",
            f"🌅 Рассвет {forecast.sunrise:%H:%M} · закат {forecast.sunset:%H:%M}",
        ]
    )
    segments = _day_segments(forecast)
    if segments:
        lines.append("🕒 " + " · ".join(segments))
    lines.append("Данные: Open-Meteo (open-meteo.com)")
    return "\n".join(lines)


def _day_segments(forecast: DailyForecast) -> tuple[str, ...]:
    if not forecast.hourly:
        return ()
    segments = []
    for hour, label in ((9, "утро"), (14, "день"), (20, "вечер")):
        point = min(forecast.hourly, key=lambda item: abs(item.at.hour - hour))
        icon, _ = _condition(point.weather_code)
        segments.append(f"{label} {icon} {_temperature(point.temperature_c)}")
    return tuple(segments)


def _temperature(value: float) -> str:
    return f"{value:+.0f}°C"


def _decimal(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def _condition(code: int) -> tuple[str, str]:
    if code == 0:
        return "☀️", "ясно"
    if code == 1:
        return "🌤", "преимущественно ясно"
    if code == 2:
        return "⛅️", "переменная облачность"
    if code == 3:
        return "☁️", "пасмурно"
    if code in {45, 48}:
        return "🌫", "туман"
    if code in {51, 53, 55, 56, 57}:
        return "🌦", "морось"
    if code in {61, 63, 65, 66, 67, 80, 81, 82}:
        return "🌧", "дождь"
    if code in {71, 73, 75, 77, 85, 86}:
        return "🌨", "снег"
    if code in {95, 96, 99}:
        return "⛈", "гроза"
    return "🌡", "погодные условия"


class WeatherApplication:
    def __init__(
        self,
        provider: WeatherForecastProvider,
        repository: WeatherRepository,
        sender: WeatherSender,
        city: str,
        chat_id: str,
        timezone: tzinfo,
        timezone_name: str,
        send_time: time,
    ) -> None:
        if not city.strip() or not chat_id.strip():
            raise ValueError("Weather requires a configured city and private chat")
        if send_time.second or send_time.microsecond:
            raise ValueError("Weather send time must use whole minutes")
        self.provider = provider
        self.repository = repository
        self.sender = sender
        self.city = city.strip()
        self.chat_id = chat_id
        self.timezone = timezone
        self.timezone_name = timezone_name
        self.send_time = send_time
        self._cached_day: date | None = None
        self._cached_at: datetime | None = None
        self._cached_text: str | None = None
        self.repository.recover_inflight()

    def today(self, now: datetime) -> str:
        local_now = now.astimezone(self.timezone)
        if (
            self._cached_day == local_now.date()
            and self._cached_at is not None
            and self._cached_text is not None
            and local_now - self._cached_at < _CACHE_TTL
        ):
            return self._cached_text
        try:
            forecast = self.provider.forecast(self.city, local_now.date(), self.timezone_name)
        except WeatherUnavailable:
            raise
        except Exception:
            raise WeatherUnavailable("Forecast is temporarily unavailable") from None
        text = render_forecast(forecast, local_now.date())
        self._cached_day = local_now.date()
        self._cached_at = local_now
        self._cached_text = text
        return text

    def run_morning(self, now: datetime) -> int:
        """Send today's forecast once, retrying explicit failures without duplicates."""
        local_now = now.astimezone(self.timezone)
        day = local_now.date()
        if local_now.time().replace(tzinfo=None) < self.send_time:
            return 0
        delivery = self.repository.get_daily(day)
        if delivery is not None:
            if delivery.status != "pending":
                return 0
            if delivery.next_attempt is not None and delivery.next_attempt > now:
                return 0
        else:
            self.repository.prepare_daily(day, now)
            delivery = self.repository.get_daily(day)
        if delivery is None:
            return 0
        message_text = delivery.text
        if delivery.text is None:
            try:
                message_text = self.today(now)
            except WeatherUnavailable as error:
                self.repository.finish_daily(day, "pending", now, now + _RETRY_DELAY)
                _LOGGER.warning(
                    "Daily weather forecast unavailable (%s); retry scheduled",
                    type(error).__name__,
                )
                return 0
            self.repository.save_daily_text(day, message_text, now)
        if not self.repository.claim_daily(day, now):
            return 0
        if message_text is None:
            return 0
        try:
            self.sender.send(self.chat_id, message_text)
        except WeatherSendRejected as error:
            self.repository.finish_daily(day, "pending", now, now + _RETRY_DELAY)
            _LOGGER.warning(
                "Daily weather message rejected (%s); retry scheduled", type(error).__name__
            )
            return 0
        except WeatherSendUncertain as error:
            self.repository.finish_daily(day, "uncertain", now)
            _LOGGER.warning(
                "Daily weather delivery uncertain (%s); automatic resend stopped",
                type(error).__name__,
            )
            return 0
        except Exception as error:
            self.repository.finish_daily(day, "uncertain", now)
            _LOGGER.warning(
                "Daily weather delivery failed (%s); automatic resend stopped",
                type(error).__name__,
            )
            return 0
        self.repository.finish_daily(day, "sent", now)
        return 1
