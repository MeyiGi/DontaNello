import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from dirty_equals import IsPartialDict

from dontanello.integrations.notion.client import NotionClient
from tests.support.http_vcr import (
    CASSETTE_DIR,
    _redact_response,
    http_vcr,
    validate_recording_uri,
)


class _SyntheticHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        size = int(self.headers.get("Content-Length", "0"))
        self.server.request_body = self.rfile.read(size)  # type: ignore[attr-defined]
        self.server.request_path = self.path  # type: ignore[attr-defined]
        self.server.request_authorization = self.headers.get("Authorization")  # type: ignore[attr-defined]
        self.send_response(200, "RESPONSE_STATUS_SENTINEL")
        self.send_header("Content-Type", "application/json; private=RESPONSE_CONTENT_TYPE_SENTINEL")
        self.send_header("Set-Cookie", "session=RESPONSE_COOKIE_SENTINEL")
        self.send_header("X-Request-Id", "RESPONSE_HEADER_SENTINEL")
        self.end_headers()
        self.wfile.write(
            json.dumps(
                {
                    "object": "user",
                    "id": "RESPONSE_ID_SENTINEL",
                    "type": "person",
                    "name": "RESPONSE_NAME_SENTINEL",
                    "person": {"email": "RESPONSE_EMAIL_SENTINEL"},
                    "access_token": "RESPONSE_ACCESS_TOKEN_SENTINEL",
                    "refresh_token": "RESPONSE_REFRESH_TOKEN_SENTINEL",
                    "password": "RESPONSE_PASSWORD_SENTINEL",
                    "private_profile_note": "RESPONSE_UNKNOWN_PRIVATE_SENTINEL",
                }
            ).encode()
        )

    def log_message(self, _format: str, *_args: object) -> None:
        pass


class VCRTransportTests(unittest.TestCase):
    def test_notion_read_contract_replays_from_synthetic_cassette(self):
        recorder = http_vcr()
        with recorder.use_cassette("notion_users_me.yaml") as cassette:
            profile = NotionClient("synthetic-test-token").request("GET", "users/me")

        self.assertTrue(cassette.all_played)
        self.assertEqual(profile["object"], "user")
        self.assertEqual(profile["type"], "person")
        self.assertEqual(profile["id"], "[REDACTED]")
        self.assertEqual(profile["person"]["email"], "[REDACTED]")

    def test_local_recording_redacts_sensitive_request_and_response_data(self):
        with ThreadingHTTPServer(("127.0.0.1", 0), _SyntheticHandler) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            with tempfile.TemporaryDirectory() as directory:
                cassette_path = Path(directory) / "synthetic.yaml"
                cassette = http_vcr(record_mode="all", allow_local_recording=True)
                request = urllib.request.Request(
                    f"http://127.0.0.1:{server.server_port}/botPATH_TOKEN_SENTINEL/getMe"
                    "?token=QUERY_TOKEN_SENTINEL",
                    data=b'{"text":"REQUEST_BODY_SENTINEL"}',
                    headers={"Authorization": "Bearer HEADER_TOKEN_SENTINEL"},
                    method="POST",
                )
                with cassette.use_cassette(str(cassette_path)):
                    with urllib.request.urlopen(request, timeout=2) as response:
                        self.assertEqual(json.load(response)["object"], "user")
                recorded = cassette_path.read_text(encoding="utf-8")
            server.shutdown()
            worker.join(timeout=2)

        self.assertEqual(server.request_authorization, "Bearer HEADER_TOKEN_SENTINEL")
        self.assertEqual(server.request_body, b'{"text":"REQUEST_BODY_SENTINEL"}')
        self.assertEqual(
            server.request_path, "/botPATH_TOKEN_SENTINEL/getMe?token=QUERY_TOKEN_SENTINEL"
        )
        interaction = yaml.safe_load(recorded)["interactions"][0]
        recorded_request = interaction["request"]
        self.assertEqual(
            recorded_request,
            IsPartialDict(
                {
                    "method": "POST",
                    "body": "[REDACTED]",
                }
            ),
        )
        self.assertEqual(
            {name.lower(): values for name, values in recorded_request["headers"].items()}[
                "authorization"
            ],
            ["[REDACTED]"],
        )
        self.assertEqual(urlsplit(recorded_request["uri"]).path, "/bot[REDACTED]/getMe")
        self.assertEqual(urlsplit(recorded_request["uri"]).query, "")
        recorded_response = interaction["response"]
        self.assertEqual(recorded_response["headers"], {"Content-Type": ["application/json"]})
        self.assertEqual(recorded_response["status"], {"code": 200, "message": "OK"})
        self.assertEqual(
            json.loads(recorded_response["body"]["string"]),
            IsPartialDict(
                {
                    "object": "user",
                    "type": "person",
                    "id": "[REDACTED]",
                    "name": "[REDACTED]",
                    "person": IsPartialDict({"email": "[REDACTED]"}),
                }
            ),
        )
        self.assertEqual(
            set(json.loads(recorded_response["body"]["string"])),
            {"object", "type", "id", "name", "person"},
        )
        self.assertEqual(set(json.loads(recorded_response["body"]["string"])["person"]), {"email"})
        for sentinel in (
            "PATH_TOKEN_SENTINEL",
            "QUERY_TOKEN_SENTINEL",
            "HEADER_TOKEN_SENTINEL",
            "REQUEST_BODY_SENTINEL",
            "RESPONSE_COOKIE_SENTINEL",
            "RESPONSE_HEADER_SENTINEL",
            "RESPONSE_ID_SENTINEL",
            "RESPONSE_NAME_SENTINEL",
            "RESPONSE_EMAIL_SENTINEL",
            "RESPONSE_ACCESS_TOKEN_SENTINEL",
            "RESPONSE_REFRESH_TOKEN_SENTINEL",
            "RESPONSE_PASSWORD_SENTINEL",
            "RESPONSE_UNKNOWN_PRIVATE_SENTINEL",
            "RESPONSE_STATUS_SENTINEL",
            "RESPONSE_CONTENT_TYPE_SENTINEL",
            "access_token",
            "refresh_token",
            "password",
            "private_profile_note",
        ):
            with self.subTest(sentinel=sentinel):
                self.assertNotIn(sentinel, recorded)
                self.assertNotIn(sentinel, repr(interaction))
        self.assertIn("[REDACTED]", recorded)

    def test_remote_recording_requires_localhost_opt_in(self):
        with self.assertRaisesRegex(ValueError, "localhost-only opt-in"):
            http_vcr(record_mode="all")
        with self.assertRaisesRegex(ValueError, "limited to localhost"):
            validate_recording_uri("https://api.notion.com/v1/users/me", allow_local_recording=True)

    def test_replay_contract_rejects_other_routes_and_userinfo(self):
        for uri in (
            "https://api.notion.com@127.0.0.1/v1/users/me",
            "https://user:password@api.notion.com/v1/users/me",
            "https://api.notion.com/v1/pages",
            "http://api.notion.com/v1/users/me",
        ):
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                validate_recording_uri(uri, allow_local_recording=False)

    def test_unknown_status_reason_is_redacted_without_changing_numeric_code(self):
        response = _redact_response(
            {
                "status": {"code": 299, "message": "UNKNOWN_STATUS_SENTINEL"},
                "headers": {"Content-Type": ["application/json; private=HEADER_SENTINEL"]},
                "body": {"string": b'{"object":"user"}'},
            }
        )

        self.assertEqual(response["status"], {"code": 299, "message": "[REDACTED]"})
        self.assertEqual(response["headers"], {"Content-Type": ["application/json"]})

    def test_committed_cassette_contains_no_private_sentinels(self):
        cassette_path = CASSETTE_DIR / "notion_users_me.yaml"
        cassette = cassette_path.read_text(encoding="utf-8")
        decoded = yaml.safe_load(cassette_path.read_text(encoding="utf-8"))
        for sentinel in (
            "synthetic-test-token",
            "PRIVATE_NAME",
            "PRIVATE_EMAIL",
            "access_token",
            "refresh_token",
        ):
            with self.subTest(sentinel=sentinel):
                self.assertNotIn(sentinel, cassette)
                self.assertNotIn(sentinel, repr(decoded))


if __name__ == "__main__":
    unittest.main()
