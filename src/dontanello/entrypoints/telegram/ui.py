"""Telegram keyboard and response presentation."""

from typing import Any

from dontanello.modules.calendar_planning import PlannerResponse
from dontanello.modules.task_capture import TaskResponse

PERSONAL_KEYBOARD = {
    "keyboard": [
        [{"text": "📋 Мои задачи"}, {"text": "📥 Inbox"}],
        [{"text": "🌤 Погода сегодня"}, {"text": "📅 Свободное время"}],
        [{"text": "⏰ Напоминания"}],
        [{"text": "⚙️ Настройки дедлайнов"}, {"text": "ℹ️ Статус"}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
    "input_field_placeholder": "Напиши задачу, напоминание или идею…",
}

KEYBOARD_COMMANDS = {
    "📋 Мои задачи": "/tasks",
    "📥 Inbox": "/inbox",
    "🌤 Погода сегодня": "/weather",
    "📅 Свободное время": "/availability",
    "⏰ Напоминания": "/reminders",
    "⚙️ Настройки дедлайнов": "/tasksettings",
    "ℹ️ Статус": "/status",
}

HELP_TEXT = (
    "DONTANELLO\n\n"
    "Можно нажимать кнопки внизу чата — команды вводить не обязательно.\n"
    "📅 СВОБОДНОЕ ВРЕМЯ\n"
    "Кнопка покажет промежутки, свободные по Google Calendar; дни можно переключать.\n"
    "🌤 ПОГОДА СЕГОДНЯ\n"
    "Покажет прогноз на сегодня; краткая сводка также приходит каждое утро.\n\n"
    "Можно написать: «Хочу позаниматься безопасностью 1,5 часа» — если день не указан, предложу время на сегодня.\n\n"
    "📥 INBOX\n"
    "Нажми «Inbox» или напиши, что сохранить: «Запиши в инбокс: узнать про аффинный шифр».\n\n"
    "✅ МОИ ЗАДАЧИ\n"
    "Кнопка «Мои задачи» покажет просроченное, задачи на сегодня и ближайшие дедлайны.\n"
    "Чтобы добавить задачу в Notion, напиши: «Добавь задачу прочитать презентацию до пятницы» — я сразу сохраню её.\n"
    "Кнопка «Настройки дедлайнов» открывает расписание уведомлений.\n\n"
    "⏰ НАПОМИНАНИЯ\n"
    "Кнопка «Напоминания» покажет активные; напиши: «Напомни завтра вечером позвонить».\n\n"
    "Кнопка «Статус» покажет состояние бота."
)


def _message_id(message: object) -> int | None:
    if not isinstance(message, dict):
        return None
    value = message.get("message_id")
    return value if isinstance(value, int) and value > 0 else None


def _telegram_markup(response: PlannerResponse | TaskResponse | None) -> dict[str, Any] | None:
    if response is None or not response.button_rows:
        return None
    return {
        "inline_keyboard": [
            [{"text": button.label, "callback_data": button.callback_data} for button in row]
            for row in response.button_rows
        ]
    }
