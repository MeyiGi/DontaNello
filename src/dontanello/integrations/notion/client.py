"""Small Notion client, using the standard library only."""

import json
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any


class NotionApiError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(f"Notion {status_code}: {message}")


class NotionClient:
    def __init__(self, token: str):
        self.token = token

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        retry_server_errors: bool = True,
    ) -> dict[str, Any]:
        for attempt in range(5):
            req = urllib.request.Request(
                "https://api.notion.com/v1/" + path,
                data=json.dumps(body).encode() if body is not None else None,
                method=method,
                headers={
                    "Authorization": "Bearer " + self.token,
                    "Notion-Version": "2025-09-03",
                    "Content-Type": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                retryable = error.code == 429 or (
                    retry_server_errors and error.code in (500, 502, 503, 504)
                )
                if retryable and attempt < 4:
                    time.sleep(min(60, float(error.headers.get("Retry-After", 2**attempt))))
                    continue
                payload = json.loads(error.read())
                raise NotionApiError(error.code, payload.get("message", "API error")) from None

        raise RuntimeError("Notion retry budget exhausted")

    def list_all(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> Iterator[dict[str, Any]]:
        cursor = None
        while True:
            if method == "POST":
                params = dict(body or {}, page_size=100)
                if cursor:
                    params["start_cursor"] = cursor
                result = self.request(method, path, params)
            else:
                suffix = "?page_size=100" + ("&start_cursor=" + cursor if cursor else "")
                result = self.request(method, path + suffix)
            yield from result["results"]
            if not result.get("has_more"):
                return
            cursor = result["next_cursor"]
