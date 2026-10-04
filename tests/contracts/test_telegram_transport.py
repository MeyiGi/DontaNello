import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from dontanello.integrations.telegram.client import (
    TelegramClient,
    TelegramRejected,
    TelegramUncertain,
)
from dontanello.modules.reports import DeliveryRejected, DeliveryUncertain
from dontanello.modules.reports.adapters.telegram import TelegramReportSender


class TelegramTransportTests(unittest.TestCase):
    def test_message_is_plain_text_and_receipt_is_returned(self):
        client = TelegramClient("fixture-not-a-real-token")
        with patch(
            "urllib.request.urlopen",
            return_value=io.BytesIO(b'{"ok":true,"result":{"message_id":7}}'),
        ) as call:
            self.assertEqual(client.send_message("123", "<task> & цель"), 7)
        payload = json.loads(call.call_args.args[0].data)
        self.assertEqual(payload["chat_id"], "123")
        self.assertEqual(payload["text"], "<task> & цель")
        self.assertNotIn("parse_mode", payload)

    def test_transport_error_is_redacted_and_ambiguous(self):
        token = "fixture-secret"
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("https://api.telegram.org/bot" + token),
        ):
            with self.assertRaises(TelegramUncertain) as raised:
                TelegramClient(token).send_message("123", "report")
        self.assertNotIn(token, str(raised.exception))

    def test_html_parse_mode_can_be_requested_explicitly(self):
        client = TelegramClient("fixture-not-a-real-token")
        with patch(
            "urllib.request.urlopen",
            return_value=io.BytesIO(b'{"ok":true,"result":{"message_id":8}}'),
        ) as call:
            client.send_message("123", "<b>Due</b>", parse_mode="HTML")
        self.assertEqual(json.loads(call.call_args.args[0].data)["parse_mode"], "HTML")

    def test_persistent_reply_keyboard_can_be_attached_to_a_message(self):
        markup = {"keyboard": [[{"text": "📈 Неделя"}]], "is_persistent": True}
        with patch(
            "urllib.request.urlopen",
            return_value=io.BytesIO(b'{"ok":true,"result":{"message_id":9}}'),
        ) as call:
            TelegramClient("fixture-not-a-real-token").send_message(
                "123", "Кнопки готовы", reply_markup=markup
            )
        payload = json.loads(call.call_args.args[0].data)
        self.assertEqual(payload["reply_markup"], markup)

    def test_set_my_commands_replaces_remote_menu_with_supported_commands(self):
        client = TelegramClient("fixture-not-a-real-token")
        commands = [{"command": "inbox", "description": "Записать идею"}]
        with patch(
            "urllib.request.urlopen",
            return_value=io.BytesIO(b'{"ok":true,"result":true}'),
        ) as call:
            client.set_my_commands(commands)
        request = call.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/setMyCommands"))
        self.assertEqual(json.loads(request.data), {"commands": commands})

    def test_http_400_rejected_but_500_uncertain(self):
        for status, expected in ((400, TelegramRejected), (500, TelegramUncertain)):
            error = urllib.error.HTTPError(
                "https://example.invalid/fixture", status, "error", {}, None
            )
            with self.subTest(status=status), patch("urllib.request.urlopen", side_effect=error):
                with self.assertRaises(expected):
                    TelegramClient("fixture").send_message("123", "report")

    def test_adapter_preserves_rejection_vs_uncertainty(self):
        client = TelegramClient("fixture")
        for source, expected in (
            (TelegramRejected, DeliveryRejected),
            (TelegramUncertain, DeliveryUncertain),
        ):
            with (
                self.subTest(source=source),
                patch.object(client, "send_message", side_effect=source("redacted")),
            ):
                with self.assertRaises(expected):
                    TelegramReportSender(client).send_message("123", "report")
