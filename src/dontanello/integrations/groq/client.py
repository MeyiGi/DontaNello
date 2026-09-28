"""Small HTTP client for Groq's OpenAI-compatible API."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

_BASE_URL = "https://api.groq.com/openai/v1"
_TIMEOUT_SECONDS = 45
_MAX_RETRIES = 2
_MAX_RETRY_AFTER_SECONDS = 120
_MAX_COMPLETION_TOKENS = 3_000


class GroqClient:
    def __init__(self, api_key: str, model: str):
        self._api_key = api_key
        self._model = model

    def complete(self, system: str, user: str) -> str:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "max_completion_tokens": _MAX_COMPLETION_TOKENS,
        }
        if self._model.startswith("openai/gpt-oss-"):
            payload["reasoning_effort"] = "medium"
            payload["include_reasoning"] = False
            payload["max_completion_tokens"] = 3000
        response = self._request("POST", "chat/completions", payload)
        try:
            decoded = json.loads(response)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RuntimeError("Groq returned invalid JSON") from None

        try:
            choice = decoded["choices"][0]
            message = choice["message"]
            content = message["content"]
        except (KeyError, IndexError, TypeError):
            raise RuntimeError("Groq returned an invalid completion response") from None

        if choice.get("finish_reason") == "length":
            raise RuntimeError("Groq summary truncated")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Groq returned an empty completion")
        return content

    def models(self) -> list[str]:
        response = self._request("GET", "models")
        try:
            decoded = json.loads(response)
            data = decoded["data"]
            models = [item["id"] for item in data]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError):
            raise RuntimeError("Groq returned an invalid models response") from None
        if any(not isinstance(model, str) for model in models):
            raise RuntimeError("Groq returned an invalid models response")
        return models

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> bytes:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{_BASE_URL}/{path}",
            data=body,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "User-Agent": "Dontanello/0.1",
            },
            method=method,
        )

        retries = 0
        while True:
            try:
                with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
                    return response.read()
            except urllib.error.HTTPError as error:
                if error.code not in (429, 503):
                    raise RuntimeError(f"Groq HTTP {error.code}") from None
                if retries >= _MAX_RETRIES:
                    raise RuntimeError(f"Groq HTTP {error.code}") from None
                delay = _retry_delay(error.code, error.headers)
                if delay is None:
                    raise RuntimeError(f"Groq HTTP {error.code}") from None
                retries += 1
                time.sleep(delay)
            except (urllib.error.URLError, TimeoutError, OSError):
                raise RuntimeError("Groq transport failed") from None


def _retry_delay(status: int, headers: Any) -> float | None:
    fallback = 30.0 if status == 429 else 1.0
    if headers is None:
        return fallback
    value = headers.get("Retry-After")
    if value is None:
        return fallback
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            seconds = (retry_at - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    if seconds < 0 or seconds > _MAX_RETRY_AFTER_SECONDS:
        return None
    return seconds
