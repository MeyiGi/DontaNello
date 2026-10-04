"""Small HTTP client for Groq's OpenAI-compatible API."""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from threading import Lock
from typing import Any

_BASE_URL = "https://api.groq.com/openai/v1"
_MAX_RETRIES = 2
_MAX_RETRY_AFTER_SECONDS = 120
_MAX_COMPLETION_TOKENS = 3_000


@dataclass(frozen=True)
class CompletionResult:
    text: str
    model: str
    usage: dict[str, Any]
    api_requests: int = 1


class GroqRequestError(RuntimeError):
    def __init__(self, message: str, api_requests: int, code: str | None = None):
        super().__init__(message)
        self.api_requests = api_requests
        self.code = code


class GroqRateLimitError(GroqRequestError):
    def __init__(self, retry_after: float | None, api_requests: int = 1):
        super().__init__("Groq HTTP 429", api_requests)
        self.retry_after = (
            retry_after
            if retry_after is not None and 0 <= retry_after <= _MAX_RETRY_AFTER_SECONDS
            else None
        )


class GroqClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        timeout: float = 45,
        max_output_tokens: int = 3_000,
        api_keys: Sequence[str] = (),
    ):
        if timeout <= 0 or max_output_tokens <= 0:
            raise ValueError("Groq request budgets must be positive")
        self._api_key = api_key
        self._keys = tuple(dict.fromkeys(key for key in (api_key, *api_keys) if key))
        if not self._keys:
            raise ValueError("Groq requires at least one API key")
        self._pool_lock = Lock()
        self._active_key = 0
        self._blocked_until: dict[int, float] = {}
        self._model = model
        self._timeout = timeout
        self._max_output_tokens = max_output_tokens

    @property
    def model(self) -> str:
        return self._model

    def complete(self, system: str, user: str, *, max_output_tokens: int | None = None) -> str:
        output_tokens = self._max_output_tokens if max_output_tokens is None else max_output_tokens
        if output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "max_completion_tokens": min(output_tokens, _MAX_COMPLETION_TOKENS),
        }
        if self._model.startswith("openai/gpt-oss-"):
            payload["reasoning_effort"] = "medium"
            payload["include_reasoning"] = False
            payload["max_completion_tokens"] = min(output_tokens, _MAX_COMPLETION_TOKENS)
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

    def structured_complete(
        self,
        system: str,
        messages: list[dict[str, str]],
        *,
        reasoning: str,
        output_schema: dict[str, Any],
        remaining_requests: int | None = None,
    ) -> CompletionResult:
        if reasoning not in {"low", "medium", "high"}:
            raise ValueError("Unsupported Groq reasoning effort")
        payload = {
            "model": self._model,
            "messages": [{"role": "system", "content": system}, *messages],
            "temperature": 0.2,
            "max_completion_tokens": self._max_output_tokens,
            "reasoning_effort": reasoning,
            "include_reasoning": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "progress_review",
                    "strict": True,
                    "schema": output_schema,
                },
            },
        }
        # The report adapter owns the three-call budget. Do not multiply it by
        # transport retries; a scheduled retry remains the application's decision.
        attempts: list[int] = []
        try:
            response = self._request(
                "POST",
                "chat/completions",
                payload,
                retry_budget=0,
                remaining_requests=remaining_requests,
                attempts=attempts,
            )
        except GroqRequestError as error:
            if error.code != "json_validate_failed":
                raise
            remaining = (
                None if remaining_requests is None else remaining_requests - error.api_requests
            )
            if remaining is not None and remaining <= 0:
                raise
            # Groq can reject an otherwise supported strict schema when the
            # generation itself fails validation. JSON mode still gives valid
            # JSON; the progress adapter then applies its full local guards.
            payload["response_format"] = {"type": "json_object"}
            try:
                response = self._request(
                    "POST",
                    "chat/completions",
                    payload,
                    retry_budget=0,
                    remaining_requests=remaining,
                    attempts=attempts,
                )
            except GroqRateLimitError as fallback_error:
                raise GroqRateLimitError(fallback_error.retry_after, len(attempts)) from None
            except GroqRequestError as fallback_error:
                raise GroqRequestError(
                    str(fallback_error), len(attempts), fallback_error.code
                ) from None
        try:
            decoded = json.loads(response)
            choice = decoded["choices"][0]
            content = choice["message"]["content"]
            model = decoded.get("model", self._model)
            usage = decoded.get("usage", {})
            if usage is None:
                usage = {}
            if not isinstance(choice, dict) or not isinstance(model, str):
                raise ValueError
            if not isinstance(usage, dict):
                raise ValueError
        except (UnicodeDecodeError, ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise GroqRequestError(
                "Groq returned an invalid structured response", len(attempts)
            ) from None
        if choice.get("finish_reason") == "length":
            raise GroqRequestError("Groq progress response truncated", len(attempts))
        if not isinstance(content, str) or not content.strip():
            raise GroqRequestError("Groq returned an empty structured response", len(attempts))
        return CompletionResult(content, model, usage, len(attempts))

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

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        retry_budget: int = _MAX_RETRIES,
        remaining_requests: int | None = None,
        attempts: list[int] | None = None,
    ) -> bytes:
        if remaining_requests is not None and remaining_requests <= 0:
            raise GroqRequestError("Groq request budget exhausted", 0)
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        used: set[int] = set()
        count = 0
        retries = 0
        key_index, wait = self._select_key(used, ignore_cooldown=path == "models")
        if key_index is None:
            raise GroqRateLimitError(wait, api_requests=0)
        while True:
            if remaining_requests is not None and count >= remaining_requests:
                raise GroqRateLimitError(wait, api_requests=count)
            request = urllib.request.Request(
                f"{_BASE_URL}/{path}",
                data=body,
                headers={
                    "Authorization": f"Bearer {self._keys[key_index]}",
                    "Content-Type": "application/json",
                    "User-Agent": "Dontanello/0.1",
                },
                method=method,
            )
            count += 1
            if attempts is not None:
                attempts.append(key_index)
            try:
                with urllib.request.urlopen(request, timeout=self._timeout) as response:
                    return response.read()
            except urllib.error.HTTPError as error:
                if error.code not in (429, 503):
                    raise GroqRequestError(
                        f"Groq HTTP {error.code}", count, _error_code(error)
                    ) from None
                if error.code == 429:
                    raw_delay = _retry_after_seconds(429, error.headers)
                    with self._pool_lock:
                        self._blocked_until[key_index] = max(
                            self._blocked_until.get(key_index, 0),
                            time.monotonic() + (raw_delay if raw_delay is not None else 30.0),
                        )
                    used.add(key_index)
                    if len(self._keys) > 1:
                        key_index, wait = self._select_key(used, ignore_cooldown=path == "models")
                        if key_index is None:
                            raise GroqRateLimitError(wait, api_requests=count) from None
                        continue
                if retries >= retry_budget:
                    if error.code == 429 and retry_budget == 0:
                        raise GroqRateLimitError(
                            _retry_delay(error.code, error.headers), count
                        ) from None
                    raise GroqRequestError(f"Groq HTTP {error.code}", count) from None
                delay = _retry_delay(error.code, error.headers)
                if delay is None:
                    raise GroqRequestError(f"Groq HTTP {error.code}", count) from None
                retries += 1
                time.sleep(delay)
            except (urllib.error.URLError, TimeoutError, OSError):
                raise GroqRequestError("Groq transport failed", count) from None

    def _select_key(
        self,
        used: set[int],
        *,
        ignore_cooldown: bool = False,
    ) -> tuple[int | None, float | None]:
        with self._pool_lock:
            now = time.monotonic()
            for offset in range(len(self._keys)):
                index = (self._active_key + offset) % len(self._keys)
                if index in used:
                    continue
                if ignore_cooldown or self._blocked_until.get(index, 0) <= now:
                    self._active_key = index
                    return index, None
            waits = [until - now for until in self._blocked_until.values() if until > now]
            return None, min(waits) if waits else None


def _retry_delay(status: int, headers: Any) -> float | None:
    seconds = _retry_after_seconds(status, headers)
    if seconds is None or seconds > _MAX_RETRY_AFTER_SECONDS:
        return None
    return seconds


def _error_code(error: urllib.error.HTTPError) -> str | None:
    """Extract only the provider's bounded machine-readable error code."""
    try:
        body = json.loads(error.read(16_384))
    except (UnicodeDecodeError, json.JSONDecodeError, OSError):
        return None
    details = body.get("error") if isinstance(body, dict) else None
    code = details.get("code") if isinstance(details, dict) else None
    if isinstance(code, str) and len(code) <= 80:
        return code
    return None


def _retry_after_seconds(status: int, headers: Any) -> float | None:
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
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return seconds
