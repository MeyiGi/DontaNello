"""Read .env/JSON, validate, and build the application settings."""

import os
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import msgspec

from .schema import Configuration, Settings
from .validation import _groq_keys, _validate


def load_settings(root: Path) -> Settings:
    """Read, validate, and assemble application settings."""
    env = _read_environment(root)
    raw, config = _read_configuration(root)
    _validate(config, env)
    return _build_settings(root, raw, env)


def _read_environment(root: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    env_file = root / ".env"

    if env_file.is_file():
        for number, line in enumerate(env_file.read_text().splitlines(), start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            key, separator, value = line.partition("=")
            if not separator:
                raise ValueError(f"{env_file}:{number}: ожидалось KEY=VALUE")
            env[key.strip()] = value.strip()

    env.update(os.environ)
    return env


def _read_configuration(root: Path) -> tuple[dict[str, Any], Configuration]:
    path = root / "config" / "settings.json"

    raw = msgspec.json.decode(path.read_bytes())

    if not isinstance(raw, dict):
        raise ValueError("Настройки должны быть объектом JSON")

    config = msgspec.convert(raw, type=Configuration, strict=True)

    return raw, config


def _chat_id(env: dict[str, str]) -> str:
    chat_id = env.get("TELEGRAM_CHAT_ID", "").strip()
    if not chat_id:
        return ""

    try:
        numeric_id = int(chat_id)
        if numeric_id > 0:
            return str(numeric_id)
    except ValueError:
        pass

    raise ValueError("TELEGRAM_CHAT_ID должен быть положительным ID личного чата")


def _calendar_secret(root: Path, raw: dict[str, Any], env: dict[str, str]) -> Path | None:
    secret = env.get("GOOGLE_CALENDAR_CLIENT_SECRET_FILE", "").strip()
    if secret:
        path = Path(secret).expanduser()
        return path if path.is_absolute() else root / path

    if not raw.get("calendar_planning"):
        return None

    matches = sorted(root.glob("client_secret_*.json"))
    return matches[0] if len(matches) == 1 else None


def _build_settings(root: Path, raw: dict[str, Any], env: dict[str, str]) -> Settings:
    keys = _groq_keys(env)
    groq_model = env.get("GROQ_MODEL", "openai/gpt-oss-120b").strip()
    planning_key = env.get("GROQ_PLANNING_API_KEY", "").strip()
    planning_model = env.get("GROQ_PLANNING_MODEL", "openai/gpt-oss-20b").strip()

    if keys and not groq_model:
        raise ValueError("GROQ_MODEL не задан")
    if planning_key and not planning_model:
        raise ValueError("GROQ_PLANNING_MODEL не задан")

    return Settings(
        root=root,
        notion_token=env["NOTION_TOKEN"],
        telegram_token=env.get("TELEGRAM_BOT_TOKEN", ""),
        timezone=ZoneInfo(env.get("TIMEZONE", "Asia/Bishkek")),
        poll_seconds=max(5, int(env.get("POLL_SECONDS", "30"))),
        config=raw,
        telegram_chat_id=_chat_id(env),
        groq_api_key=keys[0] if keys else "",
        groq_model=groq_model,
        groq_api_keys=keys,
        groq_planning_api_key=planning_key,
        groq_planning_model=planning_model,
        google_calendar_client_secret_file=_calendar_secret(root, raw, env),
        weather_city=env.get("WEATHER_CITY", "").strip(),
    )
