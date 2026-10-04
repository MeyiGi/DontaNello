"""Create pages in the configured Notion Inbox data source."""

from dataclasses import dataclass
from typing import Any

from dontanello.integrations.notion.client import NotionApiError, NotionClient

from ..models import InboxPage, InboxWriteRejected


@dataclass(frozen=True)
class NotionInboxConfig:
    data_source_id: str
    title_property: str = "Name"


@dataclass
class NotionInboxWriter:
    client: NotionClient
    config: NotionInboxConfig

    def create(self, title: str) -> InboxPage:
        try:
            result: dict[str, Any] = self.client.request(
                "POST",
                "pages",
                {
                    "parent": {
                        "type": "data_source_id",
                        "data_source_id": self.config.data_source_id,
                    },
                    "properties": {
                        self.config.title_property: {"title": [{"text": {"content": title}}]}
                    },
                },
                retry_server_errors=False,
            )
        except NotionApiError as error:
            if error.status_code < 500:
                raise InboxWriteRejected from None
            raise
        url = result.get("url")
        if not isinstance(url, str) or not url:
            raise RuntimeError("Notion did not confirm the created Inbox page")
        return InboxPage(title, url)
