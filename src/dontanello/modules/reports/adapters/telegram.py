"""Translate Telegram delivery outcomes into the report contract."""

from dataclasses import dataclass
from typing import Any

from dontanello.integrations.telegram.client import (
    TelegramClient,
    TelegramRejected,
    TelegramUncertain,
)

from ..delivery import DeliveryRejected, DeliveryUncertain


@dataclass
class TelegramReportSender:
    client: TelegramClient

    def send_message(
        self,
        chat_id: str,
        text: str,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> int:
        try:
            return self.client.send_message(
                chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup
            )
        except TelegramRejected as error:
            raise DeliveryRejected(str(error)) from None
        except TelegramUncertain as error:
            raise DeliveryUncertain(str(error)) from None
