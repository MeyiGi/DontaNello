import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dontanello.modules.weather.adapters.open_meteo import OpenMeteoWeatherProvider
from dontanello.modules.weather.adapters.sqlite import SQLiteWeatherRepository
from dontanello.modules.weather.application import WeatherApplication, render_forecast
from dontanello.modules.weather.models import (
    DailyDelivery,
    DailyForecast,
    HourlyForecast,
    WeatherSendRejected,
    WeatherSendUncertain,
    WeatherUnavailable,
)

TZ = ZoneInfo("Asia/Bishkek")


def sample_forecast() -> DailyForecast:
    return DailyForecast(
        location="Бишкек, Кыргызстан",
        current_temperature_c=7,
        apparent_temperature_c=5,
        current_weather_code=2,
        minimum_temperature_c=4,
        maximum_temperature_c=16,
        precipitation_probability=20,
        precipitation_mm=0.4,
        maximum_wind_kmh=14,
        sunrise=datetime(2026, 10, 6, 6, 45, tzinfo=TZ),
        sunset=datetime(2026, 10, 6, 18, 14, tzinfo=TZ),
        hourly=tuple(
            HourlyForecast(datetime(2026, 10, 6, hour, tzinfo=TZ), hour, 1 if hour < 15 else 3)
            for hour in (9, 14, 20)
        ),
    )


class FakeProvider:
    def __init__(self):
        self.calls = []
        self.error = None

    def forecast(self, city, day, timezone_name):
        self.calls.append((city, day, timezone_name))
        if self.error:
            raise self.error
        return sample_forecast()


class MemoryWeatherRepository:
    def __init__(self):
        self.rows = {}
        self.recoveries = 0

    def recover_inflight(self):
        self.recoveries += 1
        for day, value in tuple(self.rows.items()):
            if value.status == "sending":
                self.rows[day] = DailyDelivery("uncertain", value.text, value.next_attempt)

    def get_daily(self, day):
        return self.rows.get(day)

    def prepare_daily(self, day, now):
        self.rows.setdefault(day, DailyDelivery("pending", None, None))

    def save_daily_text(self, day, text, now):
        self.rows[day] = DailyDelivery("pending", text, None)

    def claim_daily(self, day, now):
        value = self.rows[day]
        if value.status != "pending" or (value.next_attempt and value.next_attempt > now):
            return False
        self.rows[day] = DailyDelivery("sending", value.text, None)
        return True

    def finish_daily(self, day, status, now, next_attempt=None):
        value = self.rows[day]
        self.rows[day] = DailyDelivery(status, value.text, next_attempt)


class FakeSender:
    def __init__(self, failures=()):
        self.failures = list(failures)
        self.messages = []

    def send(self, chat_id, text):
        if self.failures:
            raise self.failures.pop(0)
        self.messages.append((chat_id, text))
        return len(self.messages)


def build_app(provider=None, repository=None, sender=None):
    return WeatherApplication(
        provider or FakeProvider(),
        repository or MemoryWeatherRepository(),
        sender or FakeSender(),
        "Бишкек, Кыргызстан",
        "12345",
        TZ,
        TZ.key,
        time(6),
    )


class WeatherApplicationTests(unittest.TestCase):
    def test_rendered_summary_has_today_conditions_and_useful_details(self):
        message = render_forecast(sample_forecast(), date(2026, 10, 6))
        self.assertIn("Бишкек, Кыргызстан", message)
        self.assertIn("Вторник, 6 октября", message)
        self.assertIn("ощущается +5°C", message)
        self.assertIn("до 20%", message)
        self.assertIn("Рассвет 06:45", message)
        self.assertIn("Open-Meteo", message)

    def test_today_uses_local_date_and_caches_for_ten_minutes(self):
        provider = FakeProvider()
        app = build_app(provider=provider)
        instant = datetime(2026, 10, 5, 20, 0, tzinfo=ZoneInfo("UTC"))
        app.today(instant)
        app.today(instant + timedelta(minutes=9))
        self.assertEqual(provider.calls, [("Бишкек, Кыргызстан", date(2026, 10, 6), TZ.key)])
        app.today(instant + timedelta(minutes=11))
        self.assertEqual(len(provider.calls), 2)

    def test_morning_delivery_waits_for_time_and_is_idempotent(self):
        repo, sender, provider = MemoryWeatherRepository(), FakeSender(), FakeProvider()
        app = build_app(provider, repo, sender)
        before = datetime(2026, 10, 6, 5, 59, tzinfo=TZ)
        due = datetime(2026, 10, 6, 6, 0, tzinfo=TZ)
        self.assertEqual(app.run_morning(before), 0)
        self.assertEqual(app.run_morning(due), 1)
        self.assertEqual(app.run_morning(due + timedelta(minutes=1)), 0)
        self.assertEqual(len(sender.messages), 1)
        self.assertEqual(repo.get_daily(due.date()).status, "sent")

    def test_explicit_telegram_rejection_retries_same_saved_forecast_after_five_minutes(self):
        repo = MemoryWeatherRepository()
        provider = FakeProvider()
        sender = FakeSender([WeatherSendRejected("rejected")])
        app = build_app(provider, repo, sender)
        due = datetime(2026, 10, 6, 6, 0, tzinfo=TZ)
        self.assertEqual(app.run_morning(due), 0)
        saved_text = repo.get_daily(due.date()).text
        self.assertEqual(app.run_morning(due + timedelta(minutes=4)), 0)
        self.assertEqual(app.run_morning(due + timedelta(minutes=5)), 1)
        self.assertEqual(provider.calls.__len__(), 1)
        self.assertEqual(sender.messages, [("12345", saved_text)])

    def test_uncertain_telegram_delivery_is_not_retried(self):
        repo = MemoryWeatherRepository()
        sender = FakeSender([WeatherSendUncertain("uncertain")])
        app = build_app(repository=repo, sender=sender)
        due = datetime(2026, 10, 6, 6, 0, tzinfo=TZ)
        self.assertEqual(app.run_morning(due), 0)
        self.assertEqual(app.run_morning(due + timedelta(minutes=10)), 0)
        self.assertEqual(repo.get_daily(due.date()).status, "uncertain")
        self.assertEqual(len(sender.messages), 0)

    def test_forecast_failure_is_backed_off_and_retried(self):
        repo, provider = MemoryWeatherRepository(), FakeProvider()
        provider.error = WeatherUnavailable("offline")
        app = build_app(provider=provider, repository=repo)
        due = datetime(2026, 10, 6, 6, 0, tzinfo=TZ)
        self.assertEqual(app.run_morning(due), 0)
        provider.error = None
        self.assertEqual(app.run_morning(due + timedelta(minutes=4)), 0)
        self.assertEqual(app.run_morning(due + timedelta(minutes=5)), 1)


class OpenMeteoAdapterTests(unittest.TestCase):
    def test_geocodes_once_maps_forecast_and_uses_requested_timezone(self):
        class Gateway:
            geocodes = 0
            requests = []

            def geocode(self, city):
                self.geocodes += 1
                return {
                    "name": "Bishkek",
                    "country": "Кыргызстан",
                    "latitude": 42.87,
                    "longitude": 74.59,
                }

            def forecast(self, lat, lon, day, timezone_name):
                self.requests.append((lat, lon, day, timezone_name))
                return {
                    "current": {
                        "temperature_2m": 7,
                        "apparent_temperature": 5,
                        "weather_code": 2,
                    },
                    "daily": {
                        "temperature_2m_min": [4],
                        "temperature_2m_max": [16],
                        "precipitation_probability_max": [20],
                        "precipitation_sum": [0.4],
                        "wind_speed_10m_max": [14],
                        "sunrise": ["2026-10-06T06:45"],
                        "sunset": ["2026-10-06T18:14"],
                    },
                    "hourly": {
                        "time": ["2026-10-06T09:00"],
                        "temperature_2m": [10],
                        "weather_code": [1],
                    },
                }

        gateway = Gateway()
        provider = OpenMeteoWeatherProvider(gateway)
        first = provider.forecast("Bishkek, Kyrgyzstan", date(2026, 10, 6), TZ.key)
        second = provider.forecast("Bishkek, Kyrgyzstan", date(2026, 10, 7), TZ.key)
        self.assertEqual(first.location, "Bishkek, Кыргызстан")
        self.assertEqual(first.sunrise, datetime(2026, 10, 6, 6, 45, tzinfo=TZ))
        self.assertEqual(first.hourly[0].temperature_c, 10)
        self.assertEqual(gateway.geocodes, 1)
        self.assertEqual(gateway.requests[-1][2], date(2026, 10, 7))
        self.assertEqual(second.location, first.location)


class SQLiteWeatherRepositoryTests(unittest.TestCase):
    def test_daily_state_survives_restart_and_inflight_send_becomes_uncertain(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "weather.sqlite3"
            repo = SQLiteWeatherRepository(path)
            due = datetime(2026, 10, 6, 6, 0, tzinfo=TZ)
            repo.prepare_daily(due.date(), due)
            repo.save_daily_text(due.date(), "forecast", due)
            self.assertTrue(repo.claim_daily(due.date(), due))
            restarted = SQLiteWeatherRepository(path)
            restarted.recover_inflight()
            state = restarted.get_daily(due.date())
            self.assertEqual(state.status, "uncertain")
            self.assertEqual(state.text, "forecast")


if __name__ == "__main__":
    unittest.main()
