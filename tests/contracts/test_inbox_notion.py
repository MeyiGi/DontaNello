import unittest

from dontanello.modules.inbox.adapters.notion import NotionInboxConfig, NotionInboxWriter


class FakeNotion:
    def __init__(self):
        self.request_args = None

    def request(self, *args, **kwargs):
        self.request_args = (args, kwargs)
        return {"url": "https://notion.test/page"}


class NotionInboxContractTests(unittest.TestCase):
    def test_creates_title_only_row_under_configured_inbox_data_source_once(self):
        client = FakeNotion()
        writer = NotionInboxWriter(client, NotionInboxConfig("inbox-source", "Name"))
        result = writer.create("Что изучить")
        args, kwargs = client.request_args
        self.assertEqual(args[0:2], ("POST", "pages"))
        self.assertEqual(args[2]["parent"]["data_source_id"], "inbox-source")
        self.assertEqual(
            args[2]["properties"], {"Name": {"title": [{"text": {"content": "Что изучить"}}]}}
        )
        self.assertIs(kwargs["retry_server_errors"], False)
        self.assertEqual(result.url, "https://notion.test/page")


if __name__ == "__main__":
    unittest.main()
