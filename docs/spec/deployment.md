# Deployment

## Docker on a dedicated computer

DontaNello can run as one Docker Compose service on the computer that stays on. The container runs the personal assistant only; it does not include DontaNello Work. It reads credentials and the private weather city from the host `.env`, reads schedules from `config/settings.json`, and writes all durable application state to the host `state/` directory. Set `WEATHER_CITY` in `.env` when daily weather is enabled. The OAuth client JSON is mounted read-only from `secrets/google-calendar-client.json`.

Keep exactly one running bot process for a Telegram bot token. Before moving the service to another computer, stop the old systemd or Docker instance. Transfer the complete `state/` directory while the old process is stopped so Telegram position, task/report journals, reminders, calendar proposals, weather delivery history, OAuth token, and `state/checkboxes.json` retain continuity. Transfer `.env` and the Google OAuth client JSON separately over a secure channel; they are excluded from the Docker build context and Git.

Start the service with `docker compose --project-directory . -f deploy/docker/compose.yaml up -d --build`. Compose restarts it after failures and host reboots. Update it after `git pull` with the same command. The state directory remains on the host when the container is rebuilt or removed.

Calendar OAuth authorization is a one-time host-network operation on Linux, because the app's local OAuth callback must reach the host browser. Run `docker compose --project-directory . -f deploy/docker/compose.yaml -f deploy/docker/compose.oauth.yaml run --rm dontanello --authorize-calendar`, complete the Google consent in a browser on that computer, then start the regular service. The generated token is written to `state/google_calendar_token.json` and survives container replacement.

Do not include `.env`, `secrets/`, or `state/` in an image, Git commit, or unencrypted backup. Back up the state directory while the service is stopped; it contains private personal records and the OAuth token. Container logs are limited to three rotated 10 MiB files.

## Non-goals

- Running multiple bot replicas against the same Telegram bot token or state directory.
- Automatically provisioning Docker Engine, Google OAuth credentials, or external API credentials.
- Moving application state into a database server or splitting the modular monolith into services.
