"""Load settings explicitly; environment variables override .env."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Settings:
    root: Path
    notion_token: str = field(repr=False)
    telegram_token: str = field(repr=False)
    timezone: ZoneInfo
    poll_seconds: int
    config: dict[str, Any]
    telegram_chat_id: str = field(default="", repr=False)


def load_settings(root: Path) -> Settings:
    environment = {}
    env_file = root / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                key, value = line.split("=", 1)
                environment[key.strip()] = value.strip()
    environment.update(os.environ)
    token = environment.get("NOTION_TOKEN", "")
    if not token:
        raise ValueError("NOTION_TOKEN не задан")
    config = json.loads((root / "config" / "settings.json").read_text())
    if not isinstance(config, dict):
        raise ValueError("Настройки должны быть объектом JSON")
    sources = config.get("completion_sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("Нет настроенных баз completion_sources")
    seen = set()
    for source in sources:
        if not isinstance(source, dict) or any(
            not isinstance(source.get(key), str) or not source[key]
            for key in ("id", "name", "checkbox", "completed_on")
        ):
            raise ValueError("Некорректная настройка completion_sources")
        identity = (source["id"], source["checkbox"])
        if identity in seen:
            raise ValueError("Повторная настройка одного чекбокса")
        seen.add(identity)
    reports = config.get("reports", {})
    operations = config.get("operations", {})
    if not isinstance(reports, dict) or not isinstance(operations, dict):
        raise ValueError("Некорректные настройки reports/operations")
    if type(reports.get("enabled", False)) is not bool:
        raise ValueError("reports.enabled должен быть boolean")
    for name, default, maximum in (("hour", 9, 23), ("minute", 0, 59)):
        value = reports.get(name, default)
        if type(value) is not int or not 0 <= value <= maximum:
            raise ValueError(f"Некорректное время отчётов: {name}")
    report_sources = reports.get("sources", [])
    if not isinstance(report_sources, list) or (reports.get("enabled") and not report_sources):
        raise ValueError("Отчёты требуют sources")
    for source in report_sources:
        if not isinstance(source, dict) or any(
            not isinstance(source.get(key), str) or not source[key]
            for key in ("id", "name", "date_property", "title_property")
        ):
            raise ValueError("Некорректный источник отчёта")
        for key in ("checkbox_properties", "detail_properties", "excluded_status_values"):
            if not isinstance(source.get(key, []), list) or any(
                not isinstance(value, str) for value in source.get(key, [])
            ):
                raise ValueError(f"Источник отчёта: {key} должен быть списком строк")
    for key, default in (("backup_retention", 14), ("alert_cooldown_seconds", 3600)):
        value = operations.get(key, default)
        if type(value) is not int or value < 1:
            raise ValueError(f"operations.{key} должен быть положительным целым числом")
    chat_id = environment.get("TELEGRAM_CHAT_ID", "").strip()
    if chat_id:
        try:
            numeric_id = int(chat_id)
            if not numeric_id:
                raise ValueError
            chat_id = str(numeric_id)
        except ValueError:
            raise ValueError("TELEGRAM_CHAT_ID должен быть числовым идентификатором чата") from None
    return Settings(
        root=root,
        notion_token=token,
        telegram_token=environment.get("TELEGRAM_BOT_TOKEN", ""),
        timezone=ZoneInfo(environment.get("TIMEZONE", "Asia/Bishkek")),
        poll_seconds=max(5, int(environment.get("POLL_SECONDS", "30"))),
        config=config,
        telegram_chat_id=chat_id,
    )
