# ADR 0008: Capture notes in the Notion Inbox

## Context

The personal assistant needs a fast way to collect unprocessed ideas without creating tasks or using work databases. Telegram may replay an update after a process or delivery failure, and Notion page creation is an external side effect without an idempotency key.

## Decision

Create a small `inbox` capability. It writes one title-only page into the configured Inbox data source and stores Telegram update IDs and delivery outcomes in its own private SQLite journal. A capture is reserved before calling Notion. Confirmed requests return their stored result on replay; interrupted or ambiguous requests become `uncertain` and are not automatically repeated. Notion POST requests do not retry server errors because the remote page may already exist.

The private bot command list is set to the currently supported personal commands, removing any stale work commands. Work report sources remain unchanged.

## Consequences

- A crash after Notion accepts a page but before the local success record may require the user to find the page in Inbox; the bot will not risk silently creating a duplicate.
- The inbox capture journal is backed up and preserved during ordinary restores.
- No generic automation or note-management framework is introduced.
