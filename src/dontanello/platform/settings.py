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
    groq_api_key: str = field(default="", repr=False)
    groq_model: str = "openai/gpt-oss-120b"
    groq_api_keys: tuple[str, ...] = field(default=(), repr=False)


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
            if numeric_id <= 0:
                raise ValueError
            chat_id = str(numeric_id)
        except ValueError:
            raise ValueError("TELEGRAM_CHAT_ID должен быть положительным ID личного чата") from None
    groq_key = environment.get("GROQ_API_KEY", "").strip()
    groq_keys = tuple(
        dict.fromkeys(
            key
            for key in (
                groq_key,
                *(part.strip() for part in environment.get("GROQ_API_KEYS", "").split(",")),
            )
            if key
        )
    )
    if len(groq_keys) > 10:
        raise ValueError("GROQ_API_KEYS допускает максимум 10 уникальных ключей")
    groq_key = groq_keys[0] if groq_keys else ""
    groq_model = environment.get("GROQ_MODEL", "openai/gpt-oss-120b").strip()
    if groq_key and not groq_model:
        raise ValueError("GROQ_MODEL не задан")
    if reports.get("enabled") and not groq_key:
        raise ValueError("Включённые отчёты требуют GROQ_API_KEY")
    ai = reports.get("ai", {})
    if not isinstance(ai, dict):
        raise ValueError("reports.ai должен быть объектом")
    for name, default, maximum in (
        ("max_rounds", 3, 3),
        ("max_batch_chars", 10_000, 20_000),
        ("max_batches", 24, 48),
        ("max_requests", 48, 96),
        ("max_output_tokens", 4_500, 8_000),
        ("max_input_chars", 160_000, 500_000),
        ("max_history_records", 80, 500),
        ("timeout_seconds", 180, 600),
    ):
        value = ai.get(name, default)
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f"Некорректный лимит reports.ai.{name}")
    for name, effort_default in (("weekly_reasoning", "medium"), ("monthly_reasoning", "high")):
        if ai.get(name, effort_default) not in {"low", "medium", "high"}:
            raise ValueError(f"reports.ai.{name} должен быть low/medium/high")
    return Settings(
        root=root,
        notion_token=token,
        telegram_token=environment.get("TELEGRAM_BOT_TOKEN", ""),
        timezone=ZoneInfo(environment.get("TIMEZONE", "Asia/Bishkek")),
        poll_seconds=max(5, int(environment.get("POLL_SECONDS", "30"))),
        config=config,
        telegram_chat_id=chat_id,
        groq_api_key=groq_key,
        groq_model=groq_model,
        groq_api_keys=groq_keys,
    )
