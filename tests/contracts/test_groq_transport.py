import io
import json
import unittest
import urllib.error
from email.message import Message
from unittest.mock import patch

from dontanello.integrations.groq.client import GroqClient


def http_error(status, retry_after=None):
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError(
        "https://api.groq.com/fixture?secret=fixture-key",
        status,
        "remote response contains fixture-key",
        headers,
        io.BytesIO(b"remote response contains fixture-key"),
    )


class GroqTransportTests(unittest.TestCase):
    def test_gpt_oss_uses_medium_reasoning_and_excludes_reasoning_from_output(self):
        response = io.BytesIO(
            b'{"choices":[{"message":{"content":"summary"},"finish_reason":"stop"}]}'
        )
        with patch("urllib.request.urlopen", return_value=response) as urlopen:
            GroqClient("fixture-key", "openai/gpt-oss-120b").complete("system", "user")
        payload = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(payload["reasoning_effort"], "medium")
        self.assertFalse(payload["include_reasoning"])
        self.assertEqual(payload["max_completion_tokens"], 3000)

    def test_completion_payload_and_bearer_header(self):
        client = GroqClient("fixture-key", "fixture-model")
        response = io.BytesIO(
            b'{"choices":[{"message":{"content":"summary"},"finish_reason":"stop"}]}'
        )
        with patch("urllib.request.urlopen", return_value=response) as urlopen:
            self.assertEqual(client.complete("system prompt", "user prompt"), "summary")

        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.groq.com/openai/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer fixture-key")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 45)
        self.assertEqual(
            json.loads(request.data),
            {
                "model": "fixture-model",
                "messages": [
                    {"role": "system", "content": "system prompt"},
                    {"role": "user", "content": "user prompt"},
                ],
                "temperature": 0.2,
                "max_completion_tokens": 3000,
            },
        )

    def test_http_error_is_sanitized(self):
        secret = "fixture-key"
        with patch("urllib.request.urlopen", side_effect=http_error(400)):
            with self.assertRaisesRegex(RuntimeError, "Groq HTTP 400") as raised:
                GroqClient(secret, "fixture-model").complete("system", "user")
        self.assertNotIn(secret, str(raised.exception))
        self.assertNotIn("https://", str(raised.exception))
        self.assertNotIn("remote response", str(raised.exception))

    def test_rate_limit_retries_within_budget(self):
        good_response = io.BytesIO(
            b'{"choices":[{"message":{"content":"summary"},"finish_reason":"stop"}]}'
        )
        with (
            patch(
                "urllib.request.urlopen",
                side_effect=[http_error(429, 0), http_error(503, 0), good_response],
            ) as urlopen,
            patch("dontanello.integrations.groq.client.time.sleep") as sleep,
        ):
            result = GroqClient("fixture-key", "fixture-model").complete("system", "user")
        self.assertEqual(result, "summary")
        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.0, 0.0])

        with (
            patch("urllib.request.urlopen", side_effect=[http_error(429, 0)] * 3) as urlopen,
            patch("dontanello.integrations.groq.client.time.sleep"),
        ):
            with self.assertRaisesRegex(RuntimeError, "Groq HTTP 429"):
                GroqClient("fixture-key", "fixture-model").complete("system", "user")
        self.assertEqual(urlopen.call_count, 3)

    def test_retry_after_over_120_seconds_fails_without_sleep(self):
        with (
            patch("urllib.request.urlopen", side_effect=http_error(503, 121)) as urlopen,
            patch("dontanello.integrations.groq.client.time.sleep") as sleep,
        ):
            with self.assertRaisesRegex(RuntimeError, "Groq HTTP 503"):
                GroqClient("fixture-key", "fixture-model").complete("system", "user")
        urlopen.assert_called_once()
        sleep.assert_not_called()

    def test_45_second_retry_after_is_honored(self):
        good_response = io.BytesIO(
            b'{"choices":[{"message":{"content":"summary"},"finish_reason":"stop"}]}'
        )
        with (
            patch("urllib.request.urlopen", side_effect=[http_error(429, 45), good_response]),
            patch("dontanello.integrations.groq.client.time.sleep") as sleep,
        ):
            self.assertEqual(
                GroqClient("fixture-key", "fixture-model").complete("system", "user"),
                "summary",
            )
        sleep.assert_called_once_with(45.0)

    def test_rate_limit_without_retry_after_uses_bounded_status_defaults(self):
        for status, delay in ((429, 30.0), (503, 1.0)):
            good_response = io.BytesIO(
                b'{"choices":[{"message":{"content":"summary"},"finish_reason":"stop"}]}'
            )
            with (
                self.subTest(status=status),
                patch("urllib.request.urlopen", side_effect=[http_error(status), good_response]),
                patch("dontanello.integrations.groq.client.time.sleep") as sleep,
            ):
                self.assertEqual(
                    GroqClient("fixture-key", "fixture-model").complete("system", "user"),
                    "summary",
                )
            sleep.assert_called_once_with(delay)

    def test_long_system_and_user_inputs_are_preserved(self):
        response = io.BytesIO(
            b'{"choices":[{"message":{"content":"summary"},"finish_reason":"stop"}]}'
        )
        with patch("urllib.request.urlopen", return_value=response) as urlopen:
            system = "s" * 12_000
            user = "u" * 15_000
            GroqClient("fixture-key", "fixture-model").complete(system, user)
        request = urlopen.call_args.args[0]
        messages = json.loads(request.data)["messages"]
        self.assertEqual(messages[0]["content"], system)
        self.assertEqual(messages[1]["content"], user)

    def test_malformed_response_and_truncated_completion_are_rejected(self):
        invalid_responses = (
            b"not json",
            b'{"choices":[]}',
            b'{"choices":[{"message":{"content":" "},"finish_reason":"stop"}]}',
            b'{"choices":[{"message":{"content":"partial"},"finish_reason":"length"}]}',
        )
        expected_messages = (
            "Groq returned invalid JSON",
            "Groq returned an invalid completion response",
            "Groq returned an empty completion",
            "Groq summary truncated",
        )
        for body, message in zip(invalid_responses, expected_messages, strict=True):
            with (
                self.subTest(message=message),
                patch("urllib.request.urlopen", return_value=io.BytesIO(body)),
            ):
                with self.assertRaisesRegex(RuntimeError, message):
                    GroqClient("fixture-key", "fixture-model").complete("system", "user")

    def test_models_uses_authenticated_read_only_get(self):
        response = io.BytesIO(b'{"data":[{"id":"model-a"},{"id":"model-b"}]}')
        with patch("urllib.request.urlopen", return_value=response) as urlopen:
            self.assertEqual(GroqClient("fixture-key", "model").models(), ["model-a", "model-b"])
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.full_url, "https://api.groq.com/openai/v1/models")
        self.assertEqual(request.get_header("Authorization"), "Bearer fixture-key")


if __name__ == "__main__":
    unittest.main()
