# Notion Inbox capture

- The configured private Telegram chat can save an idea or note to the configured Notion Inbox data source using `/inbox <text>` or ordinary natural-language messages classified as Inbox by the shared Groq message-intent interpreter.
- The Inbox keyboard button starts a capture prompt. Groq classifies the next ordinary message with the pending Inbox prompt as context; a clear task, calendar, or reminder request keeps its own destination. Only an Inbox result is saved as a note. The prompt expires after 10 minutes; «отмена» cancels it. Slash commands keep their normal behavior while a prompt is pending.
- Groq may clean up spelling and wording for the title while preserving meaning, names, acronyms, numbers, and dates. If routing is ambiguous or Groq is unavailable, the bot asks the user to resend or clarify and does not save the text.
- Each capture creates one Inbox database item using its configured title property. A successful response includes the saved title and Notion page link.
- The Inbox data source and title property are configured separately from task, work, and report sources. Inbox capture never reads or writes work databases.
- Authorization is checked before parsing a capture or calling Notion; groups and other users receive no Inbox capability.
- Telegram update IDs are durable idempotency keys. Confirmed captures return the existing result when replayed. Ambiguous outcomes are never automatically repeated; the user is told to check Inbox first.
- Notion page creation is sent once when the result could be ambiguous; explicit API rejection is reported as rejected. Delivery-message retries reuse the saved capture result.
- The capture journal is private, persistent, and included in local backups. Existing capture history is preserved by default when restoring a backup.
- The Telegram command list contains only supported DontaNello personal features and replaces stale commands registered for the bot.
