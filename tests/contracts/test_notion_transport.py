import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from dontanello.integrations.notion.client import NotionApiError, NotionClient


class NotionTransportTests(unittest.TestCase):
    def test_ambiguous_page_creation_server_error_is_not_retried(self):
        error = urllib.error.HTTPError(
            "https://api.notion.com/v1/pages",
            500,
            "server error",
            {},
            io.BytesIO(json.dumps({"message": "temporary failure"}).encode()),
        )
        client = NotionClient("fixture-not-a-real-token")
        with patch("urllib.request.urlopen", side_effect=error) as call:
            with self.assertRaises(NotionApiError) as raised:
                client.request("POST", "pages", {}, retry_server_errors=False)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(raised.exception.status_code, 500)
        self.assertNotIn("fixture-not-a-real-token", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
