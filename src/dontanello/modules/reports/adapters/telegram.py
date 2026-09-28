"""Translate Telegram delivery outcomes into the report contract."""

from dataclasses import dataclass

from dontanello.integrations.telegram.client import (
    TelegramClient,
    TelegramRejected,
    TelegramUncertain,
)

from ..delivery import DeliveryRejected, DeliveryUncertain


@dataclass
class TelegramReportSender:
    client: TelegramClient

    def send_message(self, chat_id: str, text: str) -> int:
        try:
            return self.client.send_message(chat_id, text)
        except TelegramRejected as error:
            raise DeliveryRejected(str(error)) from None
        except TelegramUncertain as error:
            raise DeliveryUncertain(str(error)) from None
