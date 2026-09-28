"""Compatibility launcher for the existing systemd service and CLI."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

if __name__ == "__main__":
    from dontanello.entrypoints.cli import main

    main(ROOT)
