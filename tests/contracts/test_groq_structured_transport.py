import io
import json
import unittest
from unittest.mock import patch

from dontanello.integrations.groq.client import GroqClient, GroqRateLimitError

from .test_groq_transport import http_error


class GroqStructuredTransportTests(unittest.TestCase):
    def test_schema_reasoning_timeout_output_and_usage_are_explicit(self):
        body = json.dumps(
            {
                "model": "actual-served-model",
                "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 200, "completion_tokens": 100},
            }
        ).encode()
        schema = {"type": "object", "properties": {}, "additionalProperties": False}
        with patch("urllib.request.urlopen", return_value=io.BytesIO(body)) as urlopen:
            result = GroqClient(
                "fixture-key", "openai/gpt-oss-120b", timeout=180, max_output_tokens=3000
            ).structured_complete(
                "system",
                [{"role": "user", "content": "private evidence"}],
                reasoning="high",
                output_schema=schema,
            )
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "https://api.groq.com/openai/v1/chat/completions")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 180)
        self.assertEqual(payload["max_completion_tokens"], 3000)
        self.assertEqual(payload["reasoning_effort"], "high")
        self.assertFalse(payload["include_reasoning"])
        self.assertEqual(
            payload["response_format"],
            {
                "type": "json_schema",
                "json_schema": {"name": "progress_review", "strict": True, "schema": schema},
            },
        )
        self.assertNotIn("tools", payload)
        self.assertEqual(result.model, "actual-served-model")
        self.assertEqual(result.usage, {"prompt_tokens": 200, "completion_tokens": 100})

    def test_structured_rate_limit_is_exposed_without_hidden_transport_retries(self):
        with patch("urllib.request.urlopen", side_effect=http_error(429, 45)) as urlopen:
            with self.assertRaises(GroqRateLimitError) as raised:
                GroqClient("fixture-key", "openai/gpt-oss-120b").structured_complete(
                    "system",
                    [],
                    reasoning="medium",
                    output_schema={},
                )
        self.assertEqual(raised.exception.retry_after, 45)
        self.assertNotIn("fixture-key", str(raised.exception))
        urlopen.assert_called_once()

    def test_json_validation_failure_falls_back_to_locally_checked_json_mode(self):
        validation_error = http_error(
            400,
            body=json.dumps(
                {"error": {"code": "json_validate_failed", "message": "fixture"}}
            ).encode(),
        )
        response = io.BytesIO(
            json.dumps(
                {
                    "choices": [
                        {
                            "message": {"content": '{"findings": [], "notices": []}'},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {},
                }
            ).encode()
        )
        schema = {"type": "object", "properties": {}, "additionalProperties": False}
        with patch("urllib.request.urlopen", side_effect=[validation_error, response]) as urlopen:
            result = GroqClient("fixture-key", "openai/gpt-oss-120b").structured_complete(
                "system",
                [{"role": "user", "content": "evidence"}],
                reasoning="medium",
                output_schema=schema,
                remaining_requests=2,
            )

        strict_payload = json.loads(urlopen.call_args_list[0].args[0].data)
        fallback_payload = json.loads(urlopen.call_args_list[1].args[0].data)
        self.assertEqual(strict_payload["response_format"]["type"], "json_schema")
        self.assertEqual(fallback_payload["response_format"], {"type": "json_object"})
        self.assertEqual(result.text, '{"findings": [], "notices": []}')
        self.assertEqual(result.api_requests, 2)

    def test_json_validation_fallback_respects_shared_request_budget(self):
        validation_error = http_error(
            400,
            body=json.dumps({"error": {"code": "json_validate_failed"}}).encode(),
        )
        with patch("urllib.request.urlopen", side_effect=validation_error) as urlopen:
            with self.assertRaises(RuntimeError):
                GroqClient("fixture-key", "openai/gpt-oss-120b").structured_complete(
                    "system",
                    [],
                    reasoning="medium",
                    output_schema={},
                    remaining_requests=1,
                )
        urlopen.assert_called_once()

    def test_other_bad_requests_do_not_fallback_or_expose_provider_body(self):
        with patch("urllib.request.urlopen", side_effect=http_error(400)) as urlopen:
            with self.assertRaises(RuntimeError) as raised:
                GroqClient("fixture-key", "openai/gpt-oss-120b").structured_complete(
                    "system",
                    [],
                    reasoning="medium",
                    output_schema={},
                )
        urlopen.assert_called_once()
        self.assertNotIn("fixture-key", str(raised.exception))
        self.assertNotIn("remote response", str(raised.exception))

    def test_invalid_and_truncated_structured_responses_are_sanitized(self):
        for body in (
            b"PRIVATE_REMOTE_BODY",
            b'{"choices":[]}',
            b'{"choices":[{"message":{"content":" "}}]}',
            b'{"choices":[{"message":{"content":"PRIVATE_REMOTE_BODY"},"finish_reason":"length"}]}',
            b'{"choices":[{"message":{"content":"{}"}}],"usage":[]}',
        ):
            with (
                self.subTest(body=body),
                patch("urllib.request.urlopen", return_value=io.BytesIO(body)),
            ):
                with self.assertRaises(RuntimeError) as raised:
                    GroqClient("fixture-key", "openai/gpt-oss-120b").structured_complete(
                        "system",
                        [],
                        reasoning="medium",
                        output_schema={},
                    )
                self.assertNotIn("PRIVATE_REMOTE_BODY", str(raised.exception))

    def test_request_budgets_and_reasoning_fail_before_network(self):
        with patch("urllib.request.urlopen") as urlopen:
            for kwargs in ({"timeout": 0}, {"max_output_tokens": 0}):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    GroqClient("fixture-key", "openai/gpt-oss-120b", **kwargs)
            with self.assertRaises(ValueError):
                GroqClient("fixture-key", "openai/gpt-oss-120b").structured_complete(
                    "system",
                    [],
                    reasoning="unsupported",
                    output_schema={},
                )
        urlopen.assert_not_called()
