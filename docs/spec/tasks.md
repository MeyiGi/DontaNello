# Telegram task capture

- Only explicit personal task requests such as «добавь задачу …» or «создай задачу …» create tasks. Ordinary messages and Inbox requests are not interpreted as tasks.
- An explicit task request such as «добавь задачу …» or «создай задачу …» is written to Notion immediately; the bot replies with the created page link. Ordinary messages and Inbox requests are not interpreted as tasks.
- New tasks are created in the personal Tasks data source configured under `reminders.notion_tasks`. The default status is `Backlog 🐛`; a due date is left empty unless one was stated. Supported explicit dates include today, tomorrow, day after tomorrow, a weekday, `через N дней`, and `DD.MM[.YYYY]`.
- Creation uses the configured title, due, and status property names. It does not access DontaNello Work data sources.
- Telegram authorization is checked before task data is read or written. Requests and outcomes are durably journaled. Replayed updates return their existing result; a timeout or interrupted external write is marked uncertain and is never blindly retried.
- A successful creation response includes the Notion page link. Explicit Notion rejection is reported without claiming success.
- The task-capture journal is included in local backups. Restore preserves live creation history by default; rolling it back requires the explicit delivery-history restore option.
