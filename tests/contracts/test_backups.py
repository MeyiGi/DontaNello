import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from dontanello.modules.operations import BackupService
from dontanello.modules.operations.adapters.filesystem_backups import FileBackupStore


class BackupContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "state").mkdir()
        (self.root / "config").mkdir()
        (self.root / "state" / "checkboxes.json").write_text('{"task": true}')
        (self.root / "state" / "alerts.json").write_text("{}")
        (self.root / "state" / "telegram_cursor.json").write_text('{"offset": 10}')
        (self.root / "config" / "settings.json").write_text('{"timezone": "UTC"}')
        (self.root / ".env").write_text("TELEGRAM_BOT_TOKEN=secret")
        self.store = FileBackupStore(self.root)

    def test_daily_snapshot_is_atomic_private_and_excludes_environment(self):
        service = BackupService(self.store, retention=14)
        now = datetime(2026, 9, 28, 23, tzinfo=timezone.utc)
        self.assertEqual(service.run(now), 1)
        self.assertEqual(service.run(now), 0)
        snapshot = self.root / "state" / "backups" / "2026-09-28"
        self.store.verify("2026-09-28")
        self.assertTrue((snapshot / "state" / "checkboxes.json").exists())
        self.assertFalse((snapshot / ".env").exists())
        self.assertEqual(snapshot.stat().st_mode & 0o777, 0o700)
        self.assertEqual((snapshot / "state" / "checkboxes.json").stat().st_mode & 0o777, 0o600)

    def test_sqlite_backup_includes_committed_wal_data(self):
        database_path = self.root / "state" / "reports.sqlite3"
        connection = sqlite3.connect(database_path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE reports (value TEXT)")
        connection.execute("INSERT INTO reports VALUES ('committed in WAL')")
        connection.commit()
        try:
            self.store.create(datetime(2026, 9, 28).date())
            backup_db = self.root / "state" / "backups" / "2026-09-28" / "state" / "reports.sqlite3"
            with sqlite3.connect(backup_db) as copied:
                self.assertEqual(
                    copied.execute("SELECT value FROM reports").fetchone()[0], "committed in WAL"
                )
        finally:
            connection.close()

    def test_progress_archive_wal_data_is_backed_up_and_restored_by_default(self):
        database_path = self.root / "state" / "progress.sqlite3"
        connection = sqlite3.connect(database_path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE progress (value TEXT)")
        connection.execute("INSERT INTO progress VALUES ('before snapshot')")
        connection.commit()
        self.store.create(datetime(2026, 9, 28).date())
        connection.execute("INSERT INTO progress VALUES ('after snapshot')")
        connection.commit()
        connection.close()

        snapshot_db = self.root / "state" / "backups" / "2026-09-28" / "state" / "progress.sqlite3"
        with sqlite3.connect(snapshot_db) as backup:
            self.assertEqual(
                backup.execute("SELECT value FROM progress").fetchall(), [("before snapshot",)]
            )

        self.store.restore("2026-09-28")

        self.assertFalse(Path(str(database_path) + "-wal").exists())
        self.assertFalse(Path(str(database_path) + "-shm").exists())
        with sqlite3.connect(database_path) as restored:
            self.assertEqual(
                restored.execute("SELECT value FROM progress").fetchall(), [("before snapshot",)]
            )

    def test_reminder_history_is_backed_up_and_live_state_is_preserved_on_restore(self):
        database_path = self.root / "state" / "reminders.sqlite3"
        connection = sqlite3.connect(database_path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE reminders (text TEXT)")
        connection.execute("INSERT INTO reminders VALUES ('before snapshot')")
        connection.commit()
        self.store.create(datetime(2026, 9, 28).date())
        connection.execute("INSERT INTO reminders VALUES ('after snapshot')")
        connection.commit()
        connection.close()

        self.store.restore("2026-09-28")
        with sqlite3.connect(database_path) as current:
            self.assertEqual(
                current.execute("SELECT text FROM reminders ORDER BY text").fetchall(),
                [("after snapshot",), ("before snapshot",)],
            )

        self.store.restore("2026-09-28", restore_delivery_history=True)
        with sqlite3.connect(database_path) as restored:
            self.assertEqual(
                restored.execute("SELECT text FROM reminders").fetchall(),
                [("before snapshot",)],
            )

    def test_inbox_capture_journal_is_backed_up_and_live_history_is_preserved(self):
        database_path = self.root / "state" / "inbox.sqlite3"
        connection = sqlite3.connect(database_path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE captures (title TEXT)")
        connection.execute("INSERT INTO captures VALUES ('before snapshot')")
        connection.commit()
        self.store.create(datetime(2026, 9, 28).date())
        connection.execute("INSERT INTO captures VALUES ('after snapshot')")
        connection.commit()
        connection.close()

        snapshot_db = self.root / "state" / "backups" / "2026-09-28" / "state" / "inbox.sqlite3"
        with sqlite3.connect(snapshot_db) as backup:
            self.assertEqual(
                backup.execute("SELECT title FROM captures").fetchall(), [("before snapshot",)]
            )
        self.store.restore("2026-09-28")
        with sqlite3.connect(database_path) as current:
            self.assertEqual(
                current.execute("SELECT title FROM captures ORDER BY rowid").fetchall(),
                [("before snapshot",), ("after snapshot",)],
            )

    def test_calendar_planning_state_is_backed_up_and_live_history_is_preserved(self):
        database_path = self.root / "state" / "calendar_planning.sqlite3"
        connection = sqlite3.connect(database_path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE proposals (id TEXT)")
        connection.execute("INSERT INTO proposals VALUES ('before snapshot')")
        connection.commit()
        self.store.create(datetime(2026, 9, 28).date())
        connection.execute("INSERT INTO proposals VALUES ('after snapshot')")
        connection.commit()
        connection.close()

        self.store.restore("2026-09-28")
        with sqlite3.connect(database_path) as current:
            self.assertEqual(
                current.execute("SELECT id FROM proposals ORDER BY id").fetchall(),
                [("after snapshot",), ("before snapshot",)],
            )

        self.store.restore("2026-09-28", restore_delivery_history=True)
        with sqlite3.connect(database_path) as restored:
            self.assertEqual(
                restored.execute("SELECT id FROM proposals").fetchall(),
                [("before snapshot",)],
            )

    def test_task_capture_state_is_backed_up_and_live_history_is_preserved(self):
        database_path = self.root / "state" / "task_capture.sqlite3"
        connection = sqlite3.connect(database_path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE proposals (id TEXT)")
        connection.execute("INSERT INTO proposals VALUES ('before snapshot')")
        connection.commit()
        self.store.create(datetime(2026, 9, 28).date())
        connection.execute("INSERT INTO proposals VALUES ('after snapshot')")
        connection.commit()
        connection.close()

        self.store.restore("2026-09-28")
        with sqlite3.connect(database_path) as current:
            self.assertEqual(
                current.execute("SELECT id FROM proposals ORDER BY id").fetchall(),
                [("after snapshot",), ("before snapshot",)],
            )

        self.store.restore("2026-09-28", restore_delivery_history=True)
        with sqlite3.connect(database_path) as restored:
            self.assertEqual(
                restored.execute("SELECT id FROM proposals").fetchall(),
                [("before snapshot",)],
            )

    def test_weather_delivery_state_is_backed_up_and_live_history_is_preserved(self):
        database_path = self.root / "state" / "weather.sqlite3"
        connection = sqlite3.connect(database_path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE delivery (local_date TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO delivery VALUES ('2026-09-27')")
        connection.commit()
        self.store.create(datetime(2026, 9, 28).date())
        connection.execute("INSERT INTO delivery VALUES ('2026-09-28')")
        connection.commit()
        connection.close()

        snapshot_db = self.root / "state" / "backups" / "2026-09-28" / "state" / "weather.sqlite3"
        with sqlite3.connect(snapshot_db) as backup:
            self.assertEqual(
                backup.execute("SELECT local_date FROM delivery").fetchall(), [("2026-09-27",)]
            )

        self.store.restore("2026-09-28")
        with sqlite3.connect(database_path) as current:
            self.assertEqual(
                current.execute("SELECT local_date FROM delivery ORDER BY local_date").fetchall(),
                [("2026-09-27",), ("2026-09-28",)],
            )

        self.store.restore("2026-09-28", restore_delivery_history=True)
        with sqlite3.connect(database_path) as restored:
            self.assertEqual(
                restored.execute("SELECT local_date FROM delivery").fetchall(), [("2026-09-27",)]
            )

    def test_retention_prunes_only_owned_date_snapshots(self):
        service = BackupService(self.store, retention=2)
        for day in range(1, 5):
            service.run(datetime(2026, 9, day, tzinfo=timezone.utc))
        backups = self.root / "state" / "backups"
        foreign = backups / "2026-08-01"
        foreign.mkdir()
        (foreign / "notes.txt").write_text("keep")
        self.assertEqual(
            sorted(path.name for path in backups.iterdir() if path.is_dir()),
            ["2026-08-01", "2026-09-03", "2026-09-04"],
        )

    def test_invalid_source_fails_without_publishing_snapshot(self):
        service = BackupService(self.store, retention=1)
        # A malformed optional state file aborts before a final directory appears.
        (self.root / "state" / "alerts.json").write_text("not-json")
        with self.assertRaises(ValueError):
            service.run(datetime(2026, 9, 28, tzinfo=timezone.utc))
        backups = self.root / "state" / "backups"
        self.assertFalse((backups / "2026-09-28").exists())
        self.assertEqual(list(backups.iterdir()), [])

    def test_verify_rejects_modified_snapshot_and_path_traversal(self):
        self.store.create(datetime(2026, 9, 28).date())
        checkbox = self.root / "state" / "backups" / "2026-09-28" / "state" / "checkboxes.json"
        checkbox.write_text('{"task": false}')
        with self.assertRaises(ValueError):
            self.store.verify("2026-09-28")
        with self.assertRaises(ValueError):
            self.store.verify("../2026-09-28")

    def test_corrupt_required_checkbox_is_rejected_before_pruning(self):
        self.store.create(datetime(2026, 9, 27).date())
        (self.root / "state" / "checkboxes.json").write_text("[]")
        with self.assertRaises(ValueError):
            BackupService(self.store, retention=1).run(datetime(2026, 9, 28, tzinfo=timezone.utc))
        self.assertTrue((self.root / "state" / "backups" / "2026-09-27").exists())

    def test_corrupt_existing_daily_snapshot_is_not_treated_as_success(self):
        service = BackupService(self.store)
        now = datetime(2026, 9, 28, tzinfo=timezone.utc)
        service.run(now)
        (self.root / "state" / "backups" / "2026-09-28" / "state" / "checkboxes.json").write_text(
            "broken"
        )
        with self.assertRaises(ValueError):
            service.run(now)

    def test_retries_pruning_when_snapshot_already_exists(self):
        service = BackupService(self.store, retention=1)
        first = datetime(2026, 9, 27, tzinfo=timezone.utc)
        second = datetime(2026, 9, 28, tzinfo=timezone.utc)
        service.run(first)
        with patch.object(self.store, "prune", side_effect=OSError("temporary filesystem error")):
            with self.assertRaises(OSError):
                service.run(second)
        self.assertTrue(self.store.exists(second.date()))
        service.run(second)
        self.assertFalse(self.store.exists(first.date()))

    def test_prune_ignores_symlinked_date_directory(self):
        self.store.create(datetime(2026, 9, 28).date())
        external = self.root / "outside"
        external.mkdir()
        (external / "keep.txt").write_text("keep")
        link = self.root / "state" / "backups" / "2026-09-29"
        try:
            link.symlink_to(external, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlink unavailable: {exc}")
        self.store.prune(keep=1)
        self.assertEqual((external / "keep.txt").read_text(), "keep")

    def test_prune_does_not_count_or_delete_corrupt_recent_backup(self):
        older = datetime(2026, 9, 27).date()
        newer = datetime(2026, 9, 28).date()
        self.store.create(older)
        self.store.create(newer)
        corrupt = self.root / "state" / "backups" / newer.isoformat() / "state" / "checkboxes.json"
        corrupt.write_text("corrupted")

        self.store.prune(keep=1)

        self.assertTrue((self.root / "state" / "backups" / older.isoformat()).exists())
        self.assertTrue((self.root / "state" / "backups" / newer.isoformat()).exists())

    def test_restore_replaces_allowlisted_files_and_leaves_environment_alone(self):
        self.store.create(datetime(2026, 9, 28).date())
        (self.root / "state" / "checkboxes.json").write_text('{"task": false}')
        (self.root / "config" / "settings.json").write_text('{"timezone": "Asia/Bishkek"}')
        (self.root / ".env").write_text("TELEGRAM_BOT_TOKEN=still-secret")

        self.store.restore("2026-09-28")

        self.assertEqual((self.root / "state" / "checkboxes.json").read_text(), '{"task": true}')
        self.assertEqual(
            (self.root / "config" / "settings.json").read_text(), '{"timezone": "UTC"}'
        )
        self.assertEqual((self.root / ".env").read_text(), "TELEGRAM_BOT_TOKEN=still-secret")

    def test_restore_preserves_existing_delivery_history_by_default_and_can_restore_wal_snapshot(
        self,
    ):
        database_path = self.root / "state" / "reports.sqlite3"
        connection = sqlite3.connect(database_path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE delivery (key TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO delivery VALUES ('sent-before-snapshot')")
        connection.commit()
        self.store.create(datetime(2026, 9, 28).date())
        connection.execute("INSERT INTO delivery VALUES ('sent-after-snapshot')")
        connection.commit()
        connection.close()

        self.store.restore("2026-09-28")
        with sqlite3.connect(database_path) as current:
            self.assertEqual(
                current.execute("SELECT key FROM delivery ORDER BY key").fetchall(),
                [("sent-after-snapshot",), ("sent-before-snapshot",)],
            )

        self.store.restore("2026-09-28", restore_delivery_history=True)
        self.assertFalse(Path(str(database_path) + "-wal").exists())
        self.assertFalse(Path(str(database_path) + "-shm").exists())
        with sqlite3.connect(database_path) as restored:
            self.assertEqual(
                restored.execute("SELECT key FROM delivery ORDER BY key").fetchall(),
                [("sent-before-snapshot",)],
            )

    def test_corrupt_snapshot_leaves_all_live_files_untouched(self):
        self.store.create(datetime(2026, 9, 28).date())
        checkbox = self.root / "state" / "backups" / "2026-09-28" / "state" / "checkboxes.json"
        checkbox.write_text('{"task": false}')
        (self.root / "state" / "checkboxes.json").write_text('{"live": true}')
        (self.root / "config" / "settings.json").write_text('{"timezone": "live"}')

        with self.assertRaises(ValueError):
            self.store.restore("2026-09-28")

        self.assertEqual((self.root / "state" / "checkboxes.json").read_text(), '{"live": true}')
        self.assertEqual(
            (self.root / "config" / "settings.json").read_text(), '{"timezone": "live"}'
        )

    def test_restore_fails_closed_on_symlinked_target_directory(self):
        self.store.create(datetime(2026, 9, 28).date())
        settings = self.root / "config" / "settings.json"
        settings.unlink()
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "settings.json").write_text('{"keep": true}')
        (self.root / "config").rmdir()
        try:
            (self.root / "config").symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlink unavailable: {exc}")

        with self.assertRaises(ValueError):
            self.store.restore("2026-09-28")
        self.assertEqual((outside / "settings.json").read_text(), '{"keep": true}')


if __name__ == "__main__":
    unittest.main()
