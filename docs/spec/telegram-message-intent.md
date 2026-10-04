# Telegram message intent

The configured private Telegram chat can use ordinary Russian text to capture Inbox notes, create personal tasks, plan time in Google Calendar, or create personal reminders. One Groq message-intent interpreter chooses the destination and may improve spelling and phrasing before the message is passed to the existing capability application.

## Routing behavior

- When Groq is configured, ordinary non-command text is sent to it with the local date/time and relevant pending Inbox context. Replies that complete an already-pending calendar duration question continue through that calendar flow directly. Slash commands and keyboard actions continue through their existing deterministic handlers.
- The interpreter returns one destination (`inbox`, `task`, `calendar`, `reminder`, `clarify`, or `other`), confidence, a cleaned title where applicable, an explicit task deadline when present, and normalized calendar/reminder text.
- The interpreter proposes classification and wording only. It does not call Notion, Google Calendar, reminder storage, or Telegram APIs. Existing capability applications remain responsible for authorization, idempotency, persistence, and external side effects.
- High- and medium-confidence destinations may proceed. Low confidence, malformed output, or an unavailable model must not write to any destination; the bot asks the user to clarify or resend.
- If an Inbox prompt is pending, it is context for classification rather than an unconditional command to save. A clear calendar, task, or reminder request goes to that capability and clears the Inbox prompt. A clear Inbox intent saves one note and returns its Notion link.
- Task intents are saved immediately and return the Notion link. Explicit task deadlines are preserved; no deadline is invented.
- Calendar intents are sent to calendar planning. If a focus activity has a duration but no day, use today in the configured local timezone. Missing duration triggers its existing follow-up question; event creation still requires the existing inline confirmation and a fresh conflict check.
- Reminder intents are sent to the existing personal reminder application. The normalized request must preserve the user's requested time or daypart.
- Title cleanup must preserve meaning, proper names, acronyms, numbers, dates, and technical identifiers. The interpreter must not add a goal, deadline, duration, reminder time, or calendar time.
- Telegram's configured private-chat authorization remains before model invocation and before all reads or writes. This behavior does not grant group chats personal capabilities.

## Failure and repeat behavior

- Telegram update delivery identity remains the idempotency boundary. Replaying a delivered update must not call the model again or repeat an external write.
- If Groq cannot classify a message, no Inbox item, task, calendar event, or reminder is created. The user receives an explicit response that nothing was saved.
- The existing Groq key pool and explicit-429 failover policy apply; message routing does not introduce another provider or credential.
