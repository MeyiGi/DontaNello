"""Read-only health checks for external services and Notion schemas."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dontanello.integrations.groq.client import GroqClient
from dontanello.integrations.telegram.client import TelegramClient

if TYPE_CHECKING:
    from dontanello.bootstrap import Runtime


def check_connections(runtime: Runtime) -> None:
    for source in runtime.sources:
        source.validate()
        print("Notion: " + source.config.name + " OK")

    settings = runtime.settings
    if settings.telegram_token:
        telegram = TelegramClient(settings.telegram_token)
        print("Telegram: @" + telegram.username())
        if settings.telegram_chat_id:
            print("Telegram chat: " + telegram.chat_type(settings.telegram_chat_id) + " OK")
    else:
        print("Telegram: ожидается TELEGRAM_BOT_TOKEN в .env")

    for report_source in runtime.report_sources:
        schema = report_source.client.request("GET", "data_sources/" + report_source.config.id)
        for name, kind in (
            (report_source.config.date_property, "date"),
            (report_source.config.title_property, "title"),
        ):
            if schema["properties"].get(name, {}).get("type") != kind:
                raise ValueError(f"Report {report_source.config.name}: invalid field {name}")
        print("Report source: " + report_source.config.name + " OK")

    if runtime.task_deadline_source:
        runtime.task_deadline_source.validate()
        print("Task reminder source: OK")

    if settings.groq_api_key:
        models = GroqClient(
            settings.groq_api_key,
            settings.groq_model,
            api_keys=settings.groq_api_keys,
        ).models()
        if settings.groq_model not in models:
            raise ValueError("GROQ_MODEL недоступна")
        print("Groq: " + settings.groq_model + " OK")

    if settings.groq_planning_api_key:
        models = GroqClient(
            settings.groq_planning_api_key,
            settings.groq_planning_model,
            timeout=15,
            max_output_tokens=250,
        ).models()
        if settings.groq_planning_model not in models:
            raise ValueError("GROQ_PLANNING_MODEL недоступна")
        print("Groq planning: " + settings.groq_planning_model + " OK")
