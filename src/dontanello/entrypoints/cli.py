"""CLI entry point: parse options and delegate to application services."""

import argparse
import logging
from pathlib import Path

from dontanello.bootstrap import build_runtime, restore_backup, verify_backup
from dontanello.entrypoints.worker import run_worker
from dontanello.integrations.google_calendar.client import GoogleCalendarClient
from dontanello.modules.reports import previous_month, previous_week
from dontanello.platform.locking import worker_lock
from dontanello.platform.settings import Settings, load_settings


def _create_parser(root: Path | None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=root or Path.cwd())
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--check", action="store_true", help="Read-only connection/schema check")
    parser.add_argument(
        "--authorize-calendar",
        action="store_true",
        help="Connect this installation to Google Calendar",
    )
    parser.add_argument("--full", action="store_true", help="Full detail with --preview-report")
    parser.add_argument("--verify-backup", metavar="YYYY-MM-DD")
    parser.add_argument("--restore-backup", metavar="YYYY-MM-DD")
    parser.add_argument(
        "--restore-delivery-history",
        action="store_true",
        help="Opt in to restoring older report journal",
    )
    parser.add_argument(
        "--preview-report", choices=("week", "month"), help="Print a report without sending"
    )
    return parser


def _authorize_calendar(settings: Settings, parser: argparse.ArgumentParser) -> None:
    secret_file = settings.google_calendar_client_secret_file
    if secret_file is None:
        parser.error("calendar_planning не настроен или OAuth client file не найден")

    calendar_id = settings.config.get("calendar_planning", {}).get("calendar_id", "primary")
    client = GoogleCalendarClient(
        secret_file,
        settings.root / "state" / "google_calendar_token.json",
        calendar_id,
    )
    # Token replacement is atomic, so OAuth can run beside the polling worker.
    client.authorize()
    print("Google Calendar подключён. Токен сохранён локально с приватными правами.")


def _preview_report(settings: Settings, kind: str, full: bool) -> None:
    runtime = build_runtime(settings)
    today = runtime.now().date()
    if kind == "week":
        weekday = settings.config.get("reports", {}).get("weekly_weekday", 0)
        period = previous_week(today, weekday)
    else:
        period = previous_month(today)
    print(runtime.report(period, full=full))


def _run_worker(settings: Settings, once: bool) -> None:
    with worker_lock(settings.root / "state" / "worker.lock"):
        runtime = build_runtime(settings)
        run_worker(runtime.jobs(), settings.poll_seconds, once, runtime.failed, runtime.recovered)


def main(root: Path | None = None) -> None:
    parser = _create_parser(root)
    args = parser.parse_args()
    project_root = args.root.resolve()

    if args.restore_delivery_history and not args.restore_backup:
        parser.error("--restore-delivery-history requires --restore-backup")

    if args.verify_backup:
        verify_backup(project_root, args.verify_backup)
        print("Backup integrity OK")
        return

    if args.restore_backup:
        with worker_lock(project_root / "state" / "worker.lock"):
            restore_backup(project_root, args.restore_backup, args.restore_delivery_history)
        print("Backup restored; existing delivery history preserved unless explicitly replaced")
        return

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = load_settings(project_root)

    if args.authorize_calendar:
        _authorize_calendar(settings, parser)
    elif args.check:
        build_runtime(settings, check_only=True).check()
    elif args.preview_report:
        _preview_report(settings, args.preview_report, args.full)
    else:
        _run_worker(settings, args.once)
