import io
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from dontanello.integrations.groq.client import (
    GroqClient,
    GroqRateLimitError,
    GroqRequestError,
)

from .test_groq_transport import http_error


def response(content="{}"):
    return io.BytesIO(
        ('{"choices":[{"message":{"content":"' + content + '"},"finish_reason":"stop"}]}').encode()
    )


def structured(client, **options):
    return client.structured_complete("system", [], reasoning="medium", output_schema={}, **options)


def headers(urlopen):
    return [call.args[0].get_header("Authorization") for call in urlopen.call_args_list]


class GroqKeyRotationTests(unittest.TestCase):
    def client(self, **options):
        return GroqClient(
            "fixture-primary",
            "openai/gpt-oss-120b",
            api_keys=("fixture-secondary", "fixture-third"),
            **options,
        )

    def test_daily_quota_rotates_immediately_and_successful_key_remains_sticky(self):
        client = self.client()
        with (
            patch(
                "urllib.request.urlopen",
                side_effect=[http_error(429, 2225), response(), response()],
            ) as urlopen,
            patch("dontanello.integrations.groq.client.time.sleep") as sleep,
            patch("dontanello.integrations.groq.client.time.monotonic", return_value=100),
        ):
            first = structured(client)
            second = structured(client)
        self.assertEqual(first.api_requests, 2)
        self.assertEqual(second.api_requests, 1)
        self.assertEqual(
            headers(urlopen),
            ["Bearer fixture-primary", "Bearer fixture-secondary", "Bearer fixture-secondary"],
        )
        sleep.assert_not_called()
        self.assertNotIn("fixture-primary", repr(client))

    def test_all_keys_exhausted_are_attempted_once_then_skipped_until_cooldown_expires(self):
        client = self.client()
        with (
            patch(
                "urllib.request.urlopen",
                side_effect=[
                    http_error(429, 2225),
                    http_error(429, 3600),
                    http_error(429, 5000),
                    response(),
                ],
            ) as urlopen,
            patch("dontanello.integrations.groq.client.time.monotonic", return_value=0) as clock,
            patch("dontanello.integrations.groq.client.time.sleep") as sleep,
        ):
            with self.assertRaises(GroqRateLimitError) as raised:
                structured(client)
            self.assertEqual(raised.exception.api_requests, 3)
            self.assertIsNone(raised.exception.retry_after)
            with self.assertRaises(GroqRateLimitError) as cooldown:
                structured(client)
            self.assertEqual(cooldown.exception.api_requests, 0)
            self.assertEqual(urlopen.call_count, 3)
            clock.return_value = 2226
            self.assertEqual(structured(client).api_requests, 1)
        self.assertEqual(
            headers(urlopen),
            [
                "Bearer fixture-primary",
                "Bearer fixture-secondary",
                "Bearer fixture-third",
                "Bearer fixture-primary",
            ],
        )
        sleep.assert_not_called()

    def test_short_cooldowns_expose_earliest_wait_with_attempt_count(self):
        client = self.client()
        with (
            patch(
                "urllib.request.urlopen",
                side_effect=[http_error(429, 60), http_error(429, 45), http_error(429, 90)],
            ),
            patch("dontanello.integrations.groq.client.time.monotonic", return_value=0),
        ):
            with self.assertRaises(GroqRateLimitError) as raised:
                structured(client)
        self.assertEqual(raised.exception.retry_after, 45)
        self.assertEqual(raised.exception.api_requests, 3)

    def test_request_budget_limits_rotation_and_zero_budget_does_not_call(self):
        client = self.client()
        with patch(
            "urllib.request.urlopen", side_effect=[http_error(429, 2225), response()]
        ) as urlopen:
            with self.assertRaises(GroqRateLimitError) as raised:
                structured(client, remaining_requests=1)
            self.assertEqual(raised.exception.api_requests, 1)
            self.assertEqual(urlopen.call_count, 1)
            self.assertEqual(structured(client, remaining_requests=1).api_requests, 1)
            with self.assertRaises(GroqRequestError) as empty:
                structured(client, remaining_requests=0)
            self.assertEqual(empty.exception.api_requests, 0)
        self.assertEqual(urlopen.call_count, 2)

    def test_duplicate_keys_are_attempted_only_once_in_provided_order(self):
        client = GroqClient(
            "fixture-primary",
            "model",
            api_keys=("fixture-primary", "fixture-secondary", "fixture-secondary", ""),
        )
        with patch(
            "urllib.request.urlopen", side_effect=[http_error(429, 2225), http_error(429, 2225)]
        ) as urlopen:
            with self.assertRaises(GroqRateLimitError) as raised:
                structured(client)
        self.assertEqual(raised.exception.api_requests, 2)
        self.assertEqual(headers(urlopen), ["Bearer fixture-primary", "Bearer fixture-secondary"])

    def test_auth_server_and_network_failures_do_not_rotate(self):
        for failure in (
            http_error(401),
            http_error(403),
            http_error(503),
            urllib.error.URLError("PRIVATE_REMOTE_BODY fixture-primary"),
        ):
            with (
                self.subTest(failure=type(failure).__name__),
                patch("urllib.request.urlopen", side_effect=failure) as urlopen,
            ):
                with self.assertRaises(GroqRequestError) as raised:
                    structured(self.client())
                self.assertEqual(raised.exception.api_requests, 1)
                self.assertNotIn("fixture-primary", str(raised.exception))
                self.assertNotIn("PRIVATE_REMOTE_BODY", str(raised.exception))
                urlopen.assert_called_once()
                self.assertEqual(headers(urlopen), ["Bearer fixture-primary"])

    def test_legacy_completion_rotates_429_without_changing_string_contract(self):
        client = self.client()
        with patch(
            "urllib.request.urlopen", side_effect=[http_error(429, 2225), response("summary")]
        ) as urlopen:
            self.assertEqual(client.complete("system", "user"), "summary")
        self.assertEqual(headers(urlopen), ["Bearer fixture-primary", "Bearer fixture-secondary"])

    def test_models_read_does_not_clear_generation_cooldown(self):
        client = self.client()
        with (
            patch(
                "urllib.request.urlopen",
                side_effect=[
                    http_error(429, 2225),
                    http_error(429, 2225),
                    http_error(429, 2225),
                    io.BytesIO(b'{"data":[{"id":"model"}]}'),
                ],
            ) as urlopen,
            patch("dontanello.integrations.groq.client.time.monotonic", return_value=0),
        ):
            with self.assertRaises(GroqRateLimitError):
                structured(client)
            self.assertEqual(client.models(), ["model"])
            with self.assertRaises(GroqRateLimitError) as raised:
                structured(client)
        self.assertEqual(raised.exception.api_requests, 0)
        self.assertEqual(urlopen.call_count, 4)
        self.assertEqual(urlopen.call_args.args[0].get_method(), "GET")

    def test_concurrent_quota_failures_share_cooldown_safely(self):
        client = self.client()
        barrier = Barrier(2)

        def request(request, **kwargs):
            if request.get_header("Authorization") == "Bearer fixture-primary":
                barrier.wait(timeout=2)
                raise http_error(429, 2225)
            return response()

        with (
            patch("urllib.request.urlopen", side_effect=request),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            results = list(executor.map(lambda _: structured(client), range(2)))
        self.assertEqual([item.api_requests for item in results], [2, 2])
        with patch("urllib.request.urlopen", return_value=response()) as urlopen:
            self.assertEqual(structured(client).api_requests, 1)
        self.assertEqual(headers(urlopen), ["Bearer fixture-secondary"])
