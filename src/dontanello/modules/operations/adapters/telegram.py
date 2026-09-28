"""Telegram transport adapter for generic operational alerts."""

from dataclasses import dataclass

from dontanello.integrations.telegram.client import TelegramClient


@dataclass
class TelegramAlertSender:
    client: TelegramClient
    chat_id: str

    def send(self, text: str) -> None:
        self.client.send_message(self.chat_id, text)
