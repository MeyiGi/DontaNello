"""Translate Notion task rows into deadline observations."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from dontanello.integrations.notion.client import NotionClient

from ..models import TaskDeadline


@dataclass(frozen=True)
class NotionTaskConfig:
    source_id: str
    title_property: str = "Name"
    due_property: str = "Due"
    next_due_property: str = "Next Due"
    completed_property: str = "Completed"
    checkbox_properties: tuple[str, ...] = ("Сделано", "Готово")
    status_property: str = "List"
    excluded_status_values: tuple[str, ...] = ("Cancel ❌", "Done ✅")
    context_property: str = "Context"
    excluded_context_values: tuple[str, ...] = ("Work🕶",)


@dataclass
class NotionTaskDeadlineSource:
    client: NotionClient
    config: NotionTaskConfig
    timezone: ZoneInfo

    def validate(self) -> None:
        schema = self.client.request("GET", f"data_sources/{self.config.source_id}")
        properties = schema.get("properties", {})
        expected = {
            self.config.title_property: "title",
            self.config.due_property: "date",
            self.config.next_due_property: "formula",
            self.config.completed_property: "formula",
            self.config.status_property: "status",
            self.config.context_property: "multi_select",
        }
        for checkbox in self.config.checkbox_properties:
            expected[checkbox] = "checkbox"
        for name, kind in expected.items():
            if properties.get(name, {}).get("type") != kind:
                raise ValueError(f"Task reminder source: invalid field {name}")

    def tasks(self) -> tuple[TaskDeadline, ...]:
        pages = self.client.list_all(
            "POST", f"data_sources/{self.config.source_id}/query", {"page_size": 100}
        )
        tasks = []
        for page in pages:
            if page.get("archived") or page.get("in_trash"):
                continue
            properties = page.get("properties", {})
            due_date = _date(properties.get(self.config.due_property), self.timezone)
            if due_date is None:
                due_date = _date(properties.get(self.config.next_due_property), self.timezone)
            if due_date is None:
                continue
            identifier = page.get("id")
            if not isinstance(identifier, str) or not identifier:
                continue
            completed = any(
                isinstance(properties.get(name), dict) and properties[name].get("checkbox") is True
                for name in self.config.checkbox_properties
            ) or _formula_boolean(properties.get(self.config.completed_property))
            status = _choice(properties.get(self.config.status_property))
            contexts = _multi_select(properties.get(self.config.context_property))
            title = _title(properties.get(self.config.title_property)) or "Без названия"
            tasks.append(
                TaskDeadline(
                    id=identifier,
                    title=title,
                    due_date=due_date,
                    url=page.get("url", "") if isinstance(page.get("url", ""), str) else "",
                    completed=completed,
                    cancelled=status in self.config.excluded_status_values,
                    excluded_from_digest=bool(
                        contexts.intersection(self.config.excluded_context_values)
                    ),
                )
            )
        return tuple(tasks)


def _date(value: Any, timezone: ZoneInfo) -> date | None:
    if not isinstance(value, dict):
        return None
    raw = None
    if isinstance(value.get("date"), dict):
        raw = value["date"].get("start")
    elif isinstance(value.get("formula"), dict) and isinstance(value["formula"].get("date"), dict):
        raw = value["formula"]["date"].get("start")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        if "T" not in raw:
            return date.fromisoformat(raw)
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        local = (
            parsed.replace(tzinfo=timezone)
            if parsed.tzinfo is None
            else parsed.astimezone(timezone)
        )
        return local.date()
    except ValueError:
        return None


def _title(value: Any) -> str:
    if not isinstance(value, dict) or not isinstance(value.get("title"), list):
        return ""
    return "".join(
        part.get("plain_text", "")
        for part in value["title"]
        if isinstance(part, dict) and isinstance(part.get("plain_text", ""), str)
    ).strip()


def _formula_boolean(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("formula"), dict)
        and value["formula"].get("type") == "boolean"
        and value["formula"].get("boolean") is True
    )


def _choice(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    choice = value.get("status") or value.get("select")
    return choice.get("name", "") if isinstance(choice, dict) else ""


def _multi_select(value: Any) -> set[str]:
    if not isinstance(value, dict) or not isinstance(value.get("multi_select"), list):
        return set()
    return {
        item["name"]
        for item in value["multi_select"]
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
