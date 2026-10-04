"""Translate Telegram transport outcomes for reminder use cases."""

from dataclasses import dataclass

from dontanello.integrations.telegram.client import (
    TelegramClient,
    TelegramRejected,
    TelegramUncertain,
)

from ..models import ReminderSendRejected, ReminderSendUncertain


@dataclass
class TelegramReminderSender:
    client: TelegramClient

    def send(self, chat_id: str, text: str) -> int:
        try:
            return self.client.send_message(chat_id, text)
        except TelegramRejected as error:
            raise ReminderSendRejected(str(error)) from None
        except TelegramUncertain as error:
            raise ReminderSendUncertain(str(error)) from None
