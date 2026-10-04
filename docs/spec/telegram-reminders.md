# Telegram reminders

## Task deadline digest

- Read open personal tasks from the configured Tasks Notion data source. Use `Due`; when it is empty, use the recurring task's `Next Due`. Exclude completed, cancelled, archived, trashed, undated tasks, and tasks tagged with the configured work context (`Work🕶` by default).
- Show every overdue task, tasks due today, and upcoming tasks within the configured horizon. Group by urgency and sort by due date. Use clickable task titles instead of displaying long Notion URLs; show one compact date/relative-time label per upcoming task.
- Default schedule is daily at 06:00 in the configured user timezone, with a 7-day horizon. The user can change it in Telegram to every day or selected weekdays, change the local time and horizon, and turn the digest on/off.
- A horizon of zero means show overdue and due-today tasks, with no advance-warning days. A digest with no matching tasks is not sent.
- Send at most one digest per local calendar date. Persist the content before sending; retries reuse it. Do not blindly retry an uncertain Telegram send.
- The personal keyboard's “Мои задачи” action immediately shows overdue tasks, tasks due today, and upcoming tasks through the configured horizon. It uses the same exclusions and ordering as the scheduled digest, but still works when scheduled notifications are disabled. Always show today's weekday and date, and a distinct today section even when no task is due today. Use compact clickable task titles instead of raw URLs; express upcoming deadlines as natural remaining-time phrases (for example, “остался 1 день”). If there are no matching tasks, say there are no deadlines today and in the configured horizon.

## Personal one-time reminders

- Accept private-chat text beginning with `напомни` or `/remind`, and store the user's reminder text and local due time durably.
- Understand Russian dates (`сегодня`, `завтра`, `послезавтра`, numeric relative intervals, weekdays, and `ДД.ММ[.ГГГГ]`), explicit clock times, and these day parts: `рано утром` 07:00, `утром` 09:00, `в обед` 13:00, `после обеда` / `днём` 15:00, `вечером` 19:00, and `ночью` 22:00. An explicit clock time takes precedence over a day part. A request with a date but no time defaults to 09:00; a time-only request uses the next future occurrence.
- If the date or reminder text cannot be resolved safely, explain what is missing and do not create a reminder.
- Confirm each saved reminder with its text, resolved local date/time, and cancellable ID. `/reminders` lists pending items; `/cancelreminder ID` cancels one.
- Deliver due reminders in the same authorized private chat. Persist reminder identity and delivery state before external sends. A duplicate Telegram update must not create a duplicate reminder. Explicit Telegram rejection may be retried with backoff; uncertain delivery is not automatically repeated.

## Authorization and configuration

- Personal task access, settings, reminder creation, listing, cancellation, and delivery are restricted to the configured private Telegram chat. Authorization is checked before any Notion reads or reminder actions. Groups receive none of these capabilities.
- A persistent private-chat reply keyboard exposes the task overview, calendar availability, Inbox, reminders, deadline settings, and status. Weekly and monthly reports run on their configured schedules and are not keyboard actions; their existing explicit commands remain available. Keyboard labels route to the corresponding application use cases. It never includes work-only actions. Natural-language reminder and Inbox capture remain available without opening a menu.
- Telegram message intake uses long polling so new button presses and messages are handled as soon as Telegram delivers them, rather than waiting for a multi-second polling interval. Polling remains isolated from scheduled jobs.
- Fresh-install defaults are supplied by configuration; subsequent settings live in the reminders module's durable state. The keyboard offers direct access to the settings view; deeper settings can still be entered as text.
- Date calculations use the configured timezone. Notion and Telegram SDK details stay in adapters; scheduling and parsing rules stay in the reminder capability.
