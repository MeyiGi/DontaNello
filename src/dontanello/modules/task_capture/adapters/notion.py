"""Create confirmed task drafts in the configured personal Notion Tasks source."""

from dataclasses import dataclass
from typing import Any

from dontanello.integrations.notion.client import NotionApiError, NotionClient

from ..models import TaskDraft, TaskWriteRejected


@dataclass(frozen=True)
class NotionTaskWriterConfig:
    data_source_id: str
    title_property: str = "Name"
    due_property: str = "Due"
    status_property: str = "List"
    default_status: str = "Backlog 🐛"


@dataclass
class NotionTaskWriter:
    client: NotionClient
    config: NotionTaskWriterConfig

    def create(self, draft: TaskDraft) -> str:
        properties: dict[str, Any] = {
            self.config.title_property: {"title": [{"text": {"content": draft.title}}]},
            self.config.status_property: {"status": {"name": self.config.default_status}},
        }
        if draft.due_date is not None:
            properties[self.config.due_property] = {"date": {"start": draft.due_date.isoformat()}}
        try:
            result = self.client.request(
                "POST",
                "pages",
                {
                    "parent": {
                        "type": "data_source_id",
                        "data_source_id": self.config.data_source_id,
                    },
                    "properties": properties,
                },
                retry_server_errors=False,
            )
        except NotionApiError as error:
            if error.status_code < 500:
                raise TaskWriteRejected from None
            raise
        url = result.get("url")
        if not isinstance(url, str) or not url:
            raise RuntimeError("Notion did not confirm task creation")
        return url
