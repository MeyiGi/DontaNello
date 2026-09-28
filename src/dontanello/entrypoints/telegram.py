"""Private Telegram commands delegate to the same report use cases as schedules."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from dontanello.integrations.telegram.client import TelegramClient
from dontanello.modules.reports import (
    DeliveryService,
    Period,
    previous_month,
    previous_week,
)
from dontanello.platform.telegram_cursor import TelegramCursor


@dataclass
class TelegramCommands:
    client: TelegramClient
    cursor: TelegramCursor
    chat_id: str
    delivery: DeliveryService
    build: Callable[[Period], str]
    now: Callable[[], datetime]
    status: Callable[[], str]
    full_report: Callable[[Period], str] | None = None

    def run(self) -> int:
        offset = self.cursor.load()
        handled = 0
        for update in self.client.updates(offset):
            update_id = int(update["update_id"])
            message = update.get("message", {})
            chat = message.get("chat", {})
            # Check identity/type before reading Notion or constructing reports.
            authorized = (
                str(chat.get("id", "")) == self.chat_id
                and chat.get("type") == "private"
                and not message.get("from", {}).get("is_bot", False)
            )
            if authorized:
                command = str(message.get("text", "")).split(maxsplit=1)
                command_name = command[0].split("@", 1)[0].lower() if command else ""
                if command_name in ("/week", "/month", "/start", "/help", "/status"):
                    now = self.now()
                    key = f"command:{update_id}"
                    if self.delivery.needs_delivery(key, now):
                        existing = self.delivery.existing_text(key)
                        if existing is not None:
                            text = existing
                        elif command_name in ("/week", "/month"):
                            period = (
                                previous_week(now.date())
                                if command_name == "/week"
                                else previous_month(now.date())
                            )
                            full = len(command) > 1 and command[1].strip().lower() == "full"
                            text = (
                                self.full_report(period)
                                if full and self.full_report
                                else self.build(period)
                            )
                        elif command_name == "/status":
                            text = self.status()
                        else:
                            text = "Dontanello\n/week — предыдущая полная неделя\n/month — предыдущий месяц\n/week full и /month full — полные списки\n/status — состояние\n/help — команды"
                        self.delivery.deliver(key, text, now)
                    if not self.delivery.is_terminal(key):
                        # Safe rejection backoff: keep this update queued until due.
                        raise RuntimeError("Queued report awaiting delivery retry")
                    handled += 1
            # Failed report construction/rejected sends retain the update for retry.
            # Uncertain sends are journaled and skipped on replay, preventing blind duplicates.
            self.cursor.save(update_id + 1)
        return handled
