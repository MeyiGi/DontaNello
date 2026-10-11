"""Safe VCR.py defaults for synthetic HTTP contract tests."""

from __future__ import annotations

import json
import re
from http import HTTPStatus
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import vcr

CASSETTE_DIR = Path(__file__).parents[1] / "cassettes"
_REDACTED = "[REDACTED]"
_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
_SAFE_REQUEST_HEADERS = {"accept", "content-type", "notion-version", "user-agent"}


def validate_recording_uri(uri: str, *, allow_local_recording: bool) -> None:
    """Reject recording outside localhost fixtures or the read-only Notion profile route."""
    parts = urlsplit(uri)
    if parts.username is not None or parts.password is not None:
        raise ValueError("cassette URLs must not contain userinfo")
    host = parts.hostname
    if allow_local_recording:
        if parts.scheme != "http" or host not in _LOCAL_HOSTS:
            raise ValueError("synthetic cassette recording is limited to localhost")
    elif (
        parts.scheme != "https"
        or host != "api.notion.com"
        or parts.port is not None
        or parts.path != "/v1/users/me"
    ):
        raise ValueError("replay cassette request is outside the Notion profile contract")


def _sanitize_request(request: vcr.request.Request) -> vcr.request.Request:
    parts = urlsplit(request.uri)
    path = re.sub(r"(/bot)[^/]+(?=/)", rf"\1{_REDACTED}", parts.path, flags=re.IGNORECASE)
    request.uri = urlunsplit((parts.scheme, parts.netloc, path, "", ""))
    for name in request.headers:
        if name.lower() not in _SAFE_REQUEST_HEADERS:
            request.headers[name] = [_REDACTED]
    if request.body:
        request.body = _REDACTED
    return request


def _sanitize_profile(payload: object) -> object:
    if not isinstance(payload, dict) or payload.get("object") != "user":
        return _REDACTED

    safe: dict[str, object] = {"object": "user"}
    if "type" in payload:
        kind = payload["type"]
        safe["type"] = kind if isinstance(kind, str) and kind in {"person", "bot"} else _REDACTED
    for field in ("id", "name", "avatar_url"):
        if field in payload:
            safe[field] = _REDACTED
    person = payload.get("person")
    if isinstance(person, dict) and "email" in person:
        safe["person"] = {"email": _REDACTED}
    return safe


def _redact_response(response: dict[str, object]) -> dict[str, object]:
    response["headers"] = {"Content-Type": ["application/json"]}

    status = response.get("status")
    if isinstance(status, dict):
        code = status.get("code")
        if type(code) is int:
            try:
                message = HTTPStatus(code).phrase
            except ValueError:
                message = _REDACTED
            response["status"] = {"code": code, "message": message}
        else:
            response["status"] = {"code": _REDACTED, "message": _REDACTED}
    else:
        response["status"] = {"code": _REDACTED, "message": _REDACTED}

    body = response["body"]
    if not isinstance(body, dict):
        response["body"] = {"string": _REDACTED}
        return response
    content = body.get("string")
    if isinstance(content, (bytes, str)):
        try:
            payload = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            body["string"] = _REDACTED
        else:
            body["string"] = json.dumps(
                _sanitize_profile(payload), ensure_ascii=False, separators=(",", ":")
            ).encode()
    return response


def http_vcr(*, record_mode: str = "none", allow_local_recording: bool = False) -> vcr.VCR:
    """Build a safe recorder; non-replay recording is restricted to explicit localhost opt-in."""
    if record_mode != "none" and not allow_local_recording:
        raise ValueError("recording requires explicit localhost-only opt-in")

    def before_record_request(request: vcr.request.Request) -> vcr.request.Request:
        validate_recording_uri(request.uri, allow_local_recording=allow_local_recording)
        return _sanitize_request(request)

    return vcr.VCR(
        cassette_library_dir=str(CASSETTE_DIR),
        record_mode=record_mode,
        before_record_request=before_record_request,
        before_record_response=_redact_response,
    )
