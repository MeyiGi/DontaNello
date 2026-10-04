"""CLI composition and lifecycle; business rules live in modules."""

import argparse
import logging
from pathlib import Path

from dontanello.bootstrap import build_runtime, restore_backup, verify_backup
from dontanello.entrypoints.worker import run_worker
from dontanello.modules.reports import previous_month, previous_week
from dontanello.platform.locking import worker_lock
from dontanello.platform.settings import load_settings


def main(root: Path | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--check", action="store_true", help="Read-only connection/schema check")
    parser.add_argument("--root", type=Path, default=root or Path.cwd())
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
    runtime = build_runtime(settings)
    if args.check:
        runtime.check()
        return
    if args.preview_report:
        today = runtime.now().date()
        weekday = settings.config.get("reports", {}).get("weekly_weekday", 0)
        period = (
            previous_week(today, weekday)
            if args.preview_report == "week"
            else previous_month(today)
        )
        print(runtime.report(period, full=args.full))
        return
    with worker_lock(settings.root / "state" / "worker.lock"):
        run_worker(
            runtime.jobs(), settings.poll_seconds, args.once, runtime.failed, runtime.recovered
        )
