# Personal calendar planning

DontaNello accepts natural-language requests to reserve focused time in the owner's Google Calendar. This capability is private to the configured personal Telegram chat and is separate from DontaNello Work.

## Request and slot behavior

- The configured Google Calendar is the source of truth for busy time. Do not maintain a separate weekly schedule.
- Interpret explicit dates, durations, and time ranges deterministically when possible. An optional, separately configured Groq model may extract a structured request only when deterministic parsing cannot handle an otherwise clear planning request. Model output never creates an event directly.
- If a dated activity is clear but duration is missing, ask for the duration instead of returning a generic parse failure. Keep that intent across process restarts for 30 minutes; accept a duration-only reply or explicit cancellation, then continue with the normal calendar proposal and confirmation flow. Do not create an event while asking for clarification.
- Use the configured timezone (default `Asia/Bishkek`), planning window (default 08:00–22:00), and buffer (default 15 minutes) around existing events.
- Suggest no more than three free options, preserving time for the rest of the day where possible. If the requested fixed time conflicts or is outside the planning window, explain why and offer free options when available.
- The private Telegram keyboard's “Свободное время” action shows all free periods remaining today within the configured planning window, including the configured event buffer. Inline controls let the user inspect adjacent days or return to today. Viewing availability is read-only and never creates a calendar event.
- Suggestions are proposals only. Create a one-time calendar event only after the user presses the confirmation button. Fetch calendar events again immediately before creation; do not silently create a conflict.
- A successful creation response includes an undo button. Undo deletes only the event whose private Google Calendar proposal identity matches the stored proposal.

## Persistence and failure behavior

- Persist proposals by Telegram update ID so Telegram update replay does not create duplicate proposals. Keep the selected slot and remote event ID through process restarts.
- Use a stable Google event ID and private proposal identity so retry after an uncertain API response is idempotent. A retry verifies that identity before treating an existing event as DontaNello's.
- Keep proposal state in the calendar-planning SQLite database. Keep Telegram message delivery and inline markup in the existing durable delivery journal.
- Google credentials are obtained through an explicit local OAuth flow and stored under `state/` with owner-only permissions. OAuth client secrets and tokens are never committed.
- If Google Calendar is unauthorized or unavailable, tell the user that no event was created. Do not substitute assumptions about availability.
- Before processing messages or callbacks, Telegram verifies both the configured private chat and the individual user identity. Group chats cannot read a calendar or invoke personal actions.

## Non-goals

- No recurring events, automatic placement without confirmation, hardcoded weekly availability, sleep inference, or general-purpose AI scheduling.
- No DontaNello Work sources, accounts, or commands.
# Google Calendar access and availability

- Availability and conflict checks read events from every calendar listed for the authorized Google account with at least `reader` access. The configured `calendar_id` remains the destination for DontaNello-created events and undo.
- Calendar discovery requires `calendar.calendarlist.readonly` in addition to the existing event permission. After this scope changes, the user must complete OAuth consent again before calendar availability is available.
- If calendar discovery or an event query fails, the planner must report that it could not read the calendar; it must not treat missing access or a failed query as free time.
