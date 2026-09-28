"""Private, atomic local snapshots of explicitly allowlisted runtime data."""

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Any


class FileBackupStore:
    APP_MARKER = "dontanello-local-backup"
    ALLOWED = (
        "state/checkboxes.json",
        "state/alerts.json",
        "state/telegram_cursor.json",
        "state/reports.sqlite3",
        "config/settings.json",
    )
    REQUIRED = "state/checkboxes.json"

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.backups = self.root / "state" / "backups"

    def _snapshot(self, day: date) -> Path:
        return self.backups / day.isoformat()

    def exists(self, day: date) -> bool:
        snapshot = self._snapshot(day)
        if not snapshot.exists():
            return False
        self.verify(day.isoformat())
        return True

    @staticmethod
    def _digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _validate_json(source: Path, *, checkbox: bool = False) -> None:
        try:
            value = json.loads(source.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid JSON backup source: {source.name}") from exc
        if checkbox and (
            not isinstance(value, dict)
            or any(
                not isinstance(key, str) or type(flag) is not bool for key, flag in value.items()
            )
        ):
            raise ValueError("Invalid checkbox state")

    @staticmethod
    def _sqlite_backup(source: Path, destination: Path) -> None:
        source_uri = f"file:{source.as_posix()}?mode=ro"
        with closing(sqlite3.connect(source_uri, uri=True, timeout=10)) as source_db:
            result = source_db.execute("PRAGMA integrity_check").fetchone()
            if result is None or result[0] != "ok":
                raise ValueError("Reports database failed integrity check")
            with closing(sqlite3.connect(destination, timeout=10)) as target_db:
                source_db.backup(target_db)
                target_check = target_db.execute("PRAGMA integrity_check").fetchone()
                if target_check is None or target_check[0] != "ok":
                    raise ValueError("Snapshot database failed integrity check")

    def create(self, day: date) -> None:
        destination = self._snapshot(day)
        self.backups.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.backups, 0o700)
        if destination.exists():
            return
        if not (self.root / self.REQUIRED).is_file():
            raise FileNotFoundError(self.REQUIRED)

        staging = Path(tempfile.mkdtemp(prefix=f".{day.isoformat()}-", dir=self.backups))
        os.chmod(staging, 0o700)
        try:
            checksums: dict[str, str] = {}
            for relative in self.ALLOWED:
                source = self.root / relative
                if not source.exists():
                    continue
                if not source.is_file() or source.is_symlink():
                    raise ValueError(f"Backup source must be a regular file: {relative}")
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                if relative == "state/reports.sqlite3":
                    self._sqlite_backup(source, target)
                else:
                    self._validate_json(source, checkbox=relative == self.REQUIRED)
                    shutil.copyfile(source, target)
                os.chmod(target, 0o600)
                with target.open("rb") as snapshot_file:
                    os.fsync(snapshot_file.fileno())
                checksums[relative] = self._digest(target)

            manifest = {
                "application": self.APP_MARKER,
                "format_version": 1,
                "date": day.isoformat(),
                "files": checksums,
            }
            manifest_path = staging / "manifest.json"
            descriptor = os.open(manifest_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(manifest, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            staging.replace(destination)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def _read_manifest(self, snapshot_name: str) -> tuple[Path, dict[str, Any]]:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", snapshot_name):
            raise ValueError("Invalid snapshot name")
        snapshot = (self.backups / snapshot_name).resolve()
        if snapshot.parent != self.backups.resolve() or not snapshot.is_dir():
            raise ValueError("Snapshot not found")
        manifest_path = snapshot / "manifest.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise ValueError("Snapshot manifest is missing")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid snapshot manifest") from exc
        if (
            not isinstance(manifest, dict)
            or manifest.get("application") != self.APP_MARKER
            or manifest.get("format_version") != 1
            or manifest.get("date") != snapshot_name
            or not isinstance(manifest.get("files"), dict)
        ):
            raise ValueError("Snapshot is not a Dontanello backup")
        return snapshot, manifest

    def verify(self, snapshot_name: str) -> None:
        """Fail closed if a snapshot contains unexpected or altered files."""
        snapshot, manifest = self._read_manifest(snapshot_name)
        files = manifest["files"]
        if self.REQUIRED not in files or any(path not in self.ALLOWED for path in files):
            raise ValueError("Snapshot manifest has an invalid file list")
        actual: set[str] = set()
        for path in snapshot.rglob("*"):
            if path.is_symlink():
                raise ValueError("Snapshot contains a symbolic link")
            if path.is_file() and path != snapshot / "manifest.json":
                actual.add(path.relative_to(snapshot).as_posix())
        if actual != set(files):
            raise ValueError("Snapshot contents do not match manifest")
        for relative, checksum in files.items():
            path = snapshot / relative
            if not isinstance(checksum, str) or self._digest(path) != checksum:
                raise ValueError(f"Snapshot checksum mismatch: {relative}")
            if relative.endswith(".sqlite3"):
                with closing(
                    sqlite3.connect(f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True)
                ) as database:
                    result = database.execute("PRAGMA integrity_check").fetchone()
                    if result is None or result[0] != "ok":
                        raise ValueError("Snapshot database failed integrity check")

    def _check_restore_target(self, relative: str) -> Path:
        target = self.root / relative
        current = self.root
        parts = Path(relative).parts
        for part in parts[:-1]:
            current = current / part
            if current.is_symlink():
                raise ValueError(f"Restore path contains a symbolic link: {relative}")
            if current.exists() and not current.is_dir():
                raise ValueError(f"Restore path is not a directory: {relative}")
        if target.is_symlink():
            raise ValueError(f"Restore target is a symbolic link: {relative}")
        if target.exists() and not target.is_file():
            raise ValueError(f"Restore target is not a regular file: {relative}")
        return target

    def restore(self, snapshot_name: str, restore_delivery_history: bool = False) -> None:
        """Restore allowlisted files after staging every replacement.

        This is atomic per file, not as a group. The caller must stop workers;
        restoring report history is opt-in because rolling it back can resend
        already delivered reports.
        """
        self.verify(snapshot_name)
        snapshot, manifest = self._read_manifest(snapshot_name)
        files: dict[str, str] = manifest["files"]
        selected = [relative for relative in self.ALLOWED if relative in files]
        report_path = "state/reports.sqlite3"
        restore_database = report_path in files and (
            restore_delivery_history or not (self.root / report_path).exists()
        )
        restore_files = [
            relative for relative in selected if relative != report_path or restore_database
        ]

        targets = {relative: self._check_restore_target(relative) for relative in selected}
        if restore_database:
            for suffix in ("-wal", "-shm"):
                sidecar = self._check_restore_target(report_path + suffix)
                if sidecar.exists() and not sidecar.is_file():
                    raise ValueError(f"Restore sidecar is not a regular file: {sidecar.name}")

        staging = Path(tempfile.mkdtemp(prefix=".dontanello-restore-", dir=self.root))
        os.chmod(staging, 0o700)
        try:
            staged: dict[str, Path] = {}
            for relative in restore_files:
                stage_file = staging / relative
                stage_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copyfile(snapshot / relative, stage_file)
                os.chmod(stage_file, 0o600)
                with stage_file.open("rb") as copied:
                    os.fsync(copied.fileno())
                if self._digest(stage_file) != files[relative]:
                    raise ValueError(f"Staged restore checksum mismatch: {relative}")
                staged[relative] = stage_file

            # Create target directories only after every backup file has been
            # copied and checked. Each final replacement stays on this filesystem.
            for relative in restore_files:
                target = targets[relative]
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                self._check_restore_target(relative)
            for relative in restore_files:
                staged[relative].replace(targets[relative])
                os.chmod(targets[relative], 0o600)
                if relative == report_path:
                    for suffix in ("-wal", "-shm"):
                        (self.root / (report_path + suffix)).unlink(missing_ok=True)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def prune(self, keep: int) -> None:
        if keep < 1:
            raise ValueError("keep must be at least one")
        owned: list[tuple[str, Path]] = []
        if not self.backups.exists():
            return
        for candidate in self.backups.iterdir():
            if (
                candidate.is_symlink()
                or not candidate.is_dir()
                or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate.name)
            ):
                continue
            try:
                self.verify(candidate.name)
            except (OSError, ValueError, sqlite3.Error):
                continue
            owned.append((candidate.name, candidate))
        for _, snapshot in sorted(owned, reverse=True)[keep:]:
            shutil.rmtree(snapshot)
