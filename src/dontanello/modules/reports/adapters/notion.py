"""Read Notion data sources and translate pages into report items."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dontanello.integrations.notion.client import NotionClient

from ..models import Period, ReportItem


@dataclass(frozen=True)
class NotionReportConfig:
    id: str
    name: str
    date_property: str
    title_property: str
    checkbox_properties: tuple[str, ...] = ()
    detail_properties: tuple[str, ...] = ()
    completed_property: str = ""
    excluded_status_property: str = ""
    excluded_status_values: tuple[str, ...] = ("Cancel",)


@dataclass
class NotionReportSource:
    client: NotionClient
    config: NotionReportConfig
    timezone: ZoneInfo

    def items(self, period: Period) -> Iterator[ReportItem]:
        query = {
            "filter": {
                "and": [
                    {
                        "property": self.config.date_property,
                        "date": {"on_or_after": (period.start - timedelta(days=1)).isoformat()},
                    },
                    {
                        "property": self.config.date_property,
                        "date": {"before": (period.end + timedelta(days=1)).isoformat()},
                    },
                ]
            }
        }
        section = _section_for_name(self.config.name)
        for page in self.client.list_all("POST", f"data_sources/{self.config.id}/query", query):
            if page.get("archived") or page.get("in_trash"):
                continue
            properties = page.get("properties", {})
            date_value = properties.get(self.config.date_property)
            completed_on = _property_date(date_value, self.timezone)
            if completed_on is None or not period.start <= completed_on < period.end:
                continue
            if not self._included(properties, section):
                continue
            identifier = page.get("id")
            if not isinstance(identifier, str) or not identifier:
                continue
            yield ReportItem(
                id=identifier,
                title=_property_text(properties.get(self.config.title_property)) or "Без названия",
                completed_on=completed_on,
                url=page.get("url", "") if isinstance(page.get("url", ""), str) else "",
                section=section,
                details=_details(properties, self.config.detail_properties),
                recorded_at=_property_start(date_value, self.timezone),
            )

    def _included(self, properties: dict[str, Any], section: str) -> bool:
        if (
            section == "tasks"
            and self.config.excluded_status_property
            and _property_choice(properties.get(self.config.excluded_status_property))
            in self.config.excluded_status_values
        ):
            return False
        # Work rows describe dated activity; their checkbox state does not define completion.
        if section == "work":
            return True
        if section not in ("tasks", "goals"):
            return True
        checked = any(_checkbox(properties.get(name)) for name in self.config.checkbox_properties)
        formula_completed = (
            _formula_boolean(properties.get(self.config.completed_property))
            if self.config.completed_property
            else False
        )
        return checked or (section == "tasks" and formula_completed)


def _property_date(value: Any, timezone: ZoneInfo) -> date | None:
    recorded_at = _property_start(value, timezone)
    if not recorded_at:
        return None
    try:
        if "T" not in recorded_at:
            return date.fromisoformat(recorded_at)
        return datetime.fromisoformat(recorded_at).date()
    except ValueError:
        return None


def _property_start(value: Any, timezone: ZoneInfo) -> str:
    if not isinstance(value, dict) or not isinstance(value.get("date"), dict):
        return ""
    raw = value["date"].get("start")
    if not isinstance(raw, str) or not raw:
        return ""
    try:
        if "T" not in raw:
            return date.fromisoformat(raw).isoformat()
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        localized = (
            parsed.replace(tzinfo=timezone)
            if parsed.tzinfo is None
            else parsed.astimezone(timezone)
        )
        return localized.isoformat()
    except ValueError:
        return ""


def _property_text(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    rich_text = value.get("title") or value.get("rich_text")
    if isinstance(rich_text, list):
        return "".join(
            entry.get("plain_text", "")
            for entry in rich_text
            if isinstance(entry, dict) and isinstance(entry.get("plain_text", ""), str)
        ).strip()
    select = value.get("select") or value.get("status")
    if isinstance(select, dict) and isinstance(select.get("name"), str):
        return select["name"]
    number = value.get("number")
    if isinstance(number, (int, float)) and not isinstance(number, bool):
        return str(number)
    return ""


def _details(properties: dict[str, Any], names: tuple[str, ...]) -> str:
    parts: list[str] = []
    for name in names:
        value = properties.get(name)
        text = _property_text(value)
        if not text and isinstance(value, dict):
            if isinstance(value.get("multi_select"), list):
                text = ", ".join(
                    entry["name"]
                    for entry in value["multi_select"]
                    if isinstance(entry, dict) and isinstance(entry.get("name"), str)
                )
            elif isinstance(value.get("date"), dict):
                raw_date = value["date"].get("start")
                text = raw_date if isinstance(raw_date, str) else ""
            elif isinstance(value.get("checkbox"), bool):
                text = "Да" if value["checkbox"] else "Нет"
        if text:
            parts.append(f"{name}: {text}")
    return "; ".join(parts)


def _checkbox(value: Any) -> bool:
    return isinstance(value, dict) and value.get("checkbox") is True


def _formula_boolean(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("formula"), dict)
        and value["formula"].get("type") == "boolean"
        and value["formula"].get("boolean") is True
    )


def _property_choice(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    choice = value.get("status") or value.get("select")
    if isinstance(choice, dict) and isinstance(choice.get("name"), str):
        return choice["name"]
    return ""


def _section_for_name(name: str) -> str:
    normalized = name.casefold()
    if "task" in normalized or "задач" in normalized:
        return "tasks"
    if "goal" in normalized or "цел" in normalized:
        return "goals"
    if "work" in normalized or "работ" in normalized or "log" in normalized:
        return "work"
    return name.strip() or "other"
