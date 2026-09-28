"""Translate Notion task properties into completion observations."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date

from dontanello.integrations.notion.client import NotionClient

from ..models import Observation


@dataclass(frozen=True)
class NotionCompletionConfig:
    id: str
    name: str
    checkbox: str
    completed_on: str


@dataclass
class NotionCompletionSource:
    client: NotionClient
    config: NotionCompletionConfig

    def validate(self) -> None:
        schema = self.client.request("GET", "data_sources/" + self.config.id)
        for name, kind in ((self.config.checkbox, "checkbox"), (self.config.completed_on, "date")):
            if schema["properties"].get(name, {}).get("type") != kind:
                raise ValueError(f"{self.config.name}: поле {name} должно иметь тип {kind}")

    def observations(self) -> Iterator[Observation]:
        for page in self.client.list_all("POST", f"data_sources/{self.config.id}/query"):
            if page.get("archived") or page.get("in_trash"):
                continue
            yield Observation(
                key=f"{self.config.id}:{self.config.checkbox}:{page['id']}",
                item_id=page["id"],
                checked=page["properties"][self.config.checkbox]["checkbox"],
            )

    def is_checked(self, item_id: str) -> bool:
        page = self.client.request("GET", "pages/" + item_id)
        return bool(page["properties"][self.config.checkbox]["checkbox"])

    def stamp(self, item_id: str, completed_on: date) -> None:
        self.client.request(
            "PATCH",
            "pages/" + item_id,
            {
                "properties": {
                    self.config.completed_on: {"date": {"start": completed_on.isoformat()}}
                }
            },
        )
