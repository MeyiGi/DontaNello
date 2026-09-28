"""Durable polling position; no provider or business logic."""

import json
import os
from pathlib import Path


class TelegramCursor:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> int:
        if not self.path.exists():
            return 0
        result = json.loads(self.path.read_text())["offset"]
        if type(result) is not int or result < 0:
            raise ValueError("Invalid Telegram cursor")
        return result

    def save(self, offset: int) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w") as output:
            json.dump({"offset": offset}, output)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(self.path)
