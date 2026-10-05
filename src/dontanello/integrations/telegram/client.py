"""Telegram API transport with explicit rejection vs ambiguous delivery."""

import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any


class TelegramRejected(RuntimeError):
    """API explicitly rejected an operation; a later retry is safe."""


class TelegramUncertain(RuntimeError):
    """No reliable acknowledgement; sending again could duplicate delivery."""


class TelegramClient:
    def __init__(self, token: str):
        self.token = token
        self._send_lock = threading.Lock()
        self._last_sent = 0.0

    def request(self, method: str, body: dict[str, Any] | None = None) -> Any:
        request = urllib.request.Request(
            "https://api.telegram.org/bot" + self.token + "/" + method,
            data=json.dumps(body or {}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            # 4xx are explicit rejections; 5xx may follow an accepted side effect.
            if 400 <= error.code < 500:
                raise TelegramRejected(f"Telegram rejected {method} (HTTP {error.code})") from None
            raise TelegramUncertain(f"Telegram {method}: acknowledgement unavailable") from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            # Never include a token-bearing URL or arbitrary remote error body.
            raise TelegramUncertain(f"Telegram {method}: acknowledgement unavailable") from None
        if not isinstance(result, dict) or "ok" not in result:
            raise TelegramUncertain(f"Telegram {method}: invalid acknowledgement")
        if not result["ok"]:
            raise TelegramRejected(f"Telegram rejected {method}")
        return result.get("result")

    def username(self) -> str:
        result = self.request("getMe")
        return str(result["username"])

    def chat_type(self, chat_id: str) -> str:
        return str(self.request("getChat", {"chat_id": chat_id})["type"])

    def set_my_commands(self, commands: list[dict[str, str]]) -> None:
        self.request("setMyCommands", {"commands": commands})

    def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        self.request("answerCallbackQuery", payload)

    def edit_message_text(
        self,
        chat_id: str,
        message_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        self.request("editMessageText", payload)

    def delete_message(self, chat_id: str, message_id: int) -> None:
        self.request("deleteMessage", {"chat_id": chat_id, "message_id": message_id})

    def send_message(
        self,
        chat_id: str,
        text: str,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> int:
        with self._send_lock:
            delay = 1.1 - (time.monotonic() - self._last_sent)
            if delay > 0:
                time.sleep(delay)
            try:
                payload = {
                    "chat_id": chat_id,
                    "text": text,
                    "link_preview_options": {"is_disabled": True},
                }
                if parse_mode is not None:
                    payload["parse_mode"] = parse_mode
                if reply_markup is not None:
                    payload["reply_markup"] = reply_markup
                result = self.request("sendMessage", payload)
            finally:
                self._last_sent = time.monotonic()
        try:
            return int(result["message_id"])
        except (KeyError, TypeError, ValueError):
            raise TelegramUncertain("Telegram sendMessage: invalid acknowledgement") from None

    def updates(self, offset: int) -> list[dict[str, Any]]:
        result = self.request(
            "getUpdates",
            {
                "offset": offset,
                "timeout": 15,
                "limit": 20,
                "allowed_updates": ["message", "callback_query"],
            },
        )
        if not isinstance(result, list):
            raise TelegramUncertain("Telegram getUpdates: invalid response")
        return result
