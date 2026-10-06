"""Translate Telegram delivery failures for weather notifications."""

from dontanello.integrations.telegram.client import (
    TelegramClient,
    TelegramRejected,
    TelegramUncertain,
)

from ..models import WeatherSendRejected, WeatherSendUncertain


class TelegramWeatherSender:
    def __init__(self, client: TelegramClient) -> None:
        self.client = client

    def send(self, chat_id: str, text: str) -> int:
        try:
            return self.client.send_message(chat_id, text)
        except TelegramRejected:
            raise WeatherSendRejected("Telegram rejected the weather message") from None
        except TelegramUncertain:
            raise WeatherSendUncertain("Telegram weather delivery is uncertain") from None
