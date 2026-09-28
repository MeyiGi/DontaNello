"""Atomic JSON persistence compatible with the original watcher."""

import json
import os
from pathlib import Path


class JsonCheckboxState:
    def __init__(self, path: Path):
        self.path = path
        self.values: dict[str, bool] = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(self.values, dict) or any(
            not isinstance(key, str) or type(value) is not bool
            for key, value in self.values.items()
        ):
            raise ValueError("Invalid checkbox state; restore a valid backup")

    def get(self, key: str) -> bool | None:
        return self.values.get(key)

    def record(self, key: str, checked: bool) -> None:
        if self.values.get(key) is checked:
            return
        updated = {**self.values, key: checked}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w") as output:
            json.dump(updated, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(self.path)
        self.values = updated
