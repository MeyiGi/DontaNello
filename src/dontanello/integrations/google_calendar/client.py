"""Google OAuth and the small Calendar API surface used by DontaNello."""

from __future__ import annotations

import os
import tempfile
from importlib import import_module
from pathlib import Path
from typing import Any
from urllib.parse import quote

CALENDAR_SCOPES = ("https://www.googleapis.com/auth/calendar.events",)
_API_ROOT = "https://www.googleapis.com/calendar/v3"


class GoogleCalendarAuthorizationError(RuntimeError):
    """No valid local Google Calendar authorization is available."""


class GoogleCalendarRequestError(RuntimeError):
    """Google Calendar did not return a usable result."""


class GoogleCalendarClient:
    def __init__(self, client_secret_file: Path, token_file: Path, calendar_id: str = "primary"):
        self.client_secret_file = client_secret_file
        self.token_file = token_file
        self.calendar_id = calendar_id
        self._saved_token: str | None = None

    def authorize(self) -> None:
        try:
            installed_flow = import_module("google_auth_oauthlib.flow").InstalledAppFlow
        except ImportError:
            raise GoogleCalendarRequestError(
                "Install project dependencies before authorizing Google Calendar"
            ) from None
        if not self.client_secret_file.is_file():
            raise GoogleCalendarAuthorizationError("Google OAuth client file is missing")
        flow = installed_flow.from_client_secrets_file(
            str(self.client_secret_file), scopes=CALENDAR_SCOPES
        )
        try:
            credentials = flow.run_local_server(
                host="localhost",
                port=0,
                open_browser=False,
                authorization_prompt_message="Открой в браузере ссылку авторизации: {url}",
                success_message="Google Calendar подключён. Можно закрыть эту вкладку.",
            )
            self._save_credentials(credentials)
        except Exception:
            raise GoogleCalendarAuthorizationError(
                "Google Calendar authorization was not completed"
            ) from None

    def events(self, start: str, end: str, timezone: str) -> tuple[dict[str, Any], ...]:
        path = self._calendar_path() + "/events"
        params: dict[str, str] = {
            "timeMin": start,
            "timeMax": end,
            "timeZone": timezone,
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": "2500",
        }
        result: list[dict[str, Any]] = []
        while True:
            page = self._request("GET", path, params=params)
            items = page.get("items", [])
            if not isinstance(items, list):
                raise GoogleCalendarRequestError("Google Calendar returned an invalid event list")
            result.extend(item for item in items if isinstance(item, dict))
            page_token = page.get("nextPageToken")
            if not page_token:
                return tuple(result)
            params["pageToken"] = str(page_token)

    def insert_event(
        self,
        event_id: str,
        proposal_id: str,
        title: str,
        start: str,
        end: str,
        timezone: str,
    ) -> str:
        body = {
            "id": event_id,
            "summary": title,
            "start": {"dateTime": start, "timeZone": timezone},
            "end": {"dateTime": end, "timeZone": timezone},
            "extendedProperties": {"private": {"dontanelloProposalId": proposal_id}},
        }
        try:
            result = self._request("POST", self._calendar_path() + "/events", body=body)
        except _CalendarApiError as error:
            if error.status != 409:
                raise GoogleCalendarRequestError(
                    "Google Calendar could not confirm event creation"
                ) from None
            existing = self.get_event(event_id)
            if existing is None or not _belongs_to(existing, proposal_id):
                raise GoogleCalendarRequestError(
                    "Google Calendar event identity did not match"
                ) from None
            result = existing
        return str(result.get("id", event_id))

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        try:
            return self._request(
                "GET", self._calendar_path() + "/events/" + quote(event_id, safe="")
            )
        except _CalendarApiError as error:
            if error.status == 404:
                return None
            raise GoogleCalendarRequestError("Google Calendar could not verify the event") from None

    def delete_created_event(self, event_id: str, proposal_id: str) -> bool:
        existing = self.get_event(event_id)
        if existing is None:
            return True
        if not _belongs_to(existing, proposal_id):
            return False
        try:
            self._request("DELETE", self._calendar_path() + "/events/" + quote(event_id, safe=""))
        except _CalendarApiError as error:
            if error.status != 404:
                raise GoogleCalendarRequestError(
                    "Google Calendar could not confirm event deletion"
                ) from None
        return True

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            google_errors = import_module("google.auth.exceptions")
            authorized_session = import_module("google.auth.transport.requests").AuthorizedSession
        except ImportError:
            raise GoogleCalendarRequestError(
                "Install project dependencies before using Google Calendar"
            ) from None
        credentials = self._credentials()
        session = authorized_session(credentials)
        try:
            response = session.request(
                method,
                _API_ROOT + path,
                params=params,
                json=body,
                timeout=20,
            )
            if credentials.token and credentials.token != self._saved_token:
                self._save_credentials(credentials)
            if response.status_code >= 400:
                if response.status_code == 401:
                    raise GoogleCalendarAuthorizationError("Google Calendar authorization expired")
                raise _CalendarApiError(response.status_code)
            if response.status_code == 204:
                return {}
            result = response.json()
        except _CalendarApiError:
            raise
        except GoogleCalendarAuthorizationError:
            raise
        except google_errors.RefreshError:
            raise GoogleCalendarAuthorizationError(
                "Google Calendar authorization expired"
            ) from None
        except Exception:
            raise GoogleCalendarRequestError("Google Calendar is temporarily unavailable") from None
        if not isinstance(result, dict):
            raise GoogleCalendarRequestError("Google Calendar returned an invalid response")
        return result

    def _credentials(self) -> Any:
        try:
            request_type = import_module("google.auth.transport.requests").Request
            credentials_type = import_module("google.oauth2.credentials").Credentials
        except ImportError:
            raise GoogleCalendarRequestError(
                "Install project dependencies before using Google Calendar"
            ) from None
        if not self.token_file.is_file():
            raise GoogleCalendarAuthorizationError("Google Calendar has not been authorized")
        try:
            credentials = credentials_type.from_authorized_user_file(
                str(self.token_file), scopes=CALENDAR_SCOPES
            )
            self._saved_token = credentials.token
            if not credentials.valid and credentials.expired and credentials.refresh_token:
                credentials.refresh(request_type())
                self._save_credentials(credentials)
            if not credentials.valid:
                raise GoogleCalendarAuthorizationError(
                    "Google Calendar authorization must be renewed"
                )
            return credentials
        except GoogleCalendarAuthorizationError:
            raise
        except Exception:
            raise GoogleCalendarAuthorizationError(
                "Google Calendar authorization must be renewed"
            ) from None

    def _save_credentials(self, credentials: Any) -> None:
        self.token_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="google-calendar-", suffix=".tmp", dir=self.token_file.parent
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w") as output:
                output.write(credentials.to_json())
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_name, self.token_file)
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            Path(temporary_name).unlink(missing_ok=True)
            raise

    def _calendar_path(self) -> str:
        return "/calendars/" + quote(self.calendar_id, safe="")


class _CalendarApiError(RuntimeError):
    def __init__(self, status: int):
        self.status = status


def _belongs_to(event: dict[str, Any], proposal_id: str) -> bool:
    properties = event.get("extendedProperties", {})
    private = properties.get("private", {}) if isinstance(properties, dict) else {}
    return isinstance(private, dict) and private.get("dontanelloProposalId") == proposal_id
