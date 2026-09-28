"""Fail closed on staged secrets without printing their values."""

import argparse
import re
import subprocess
from pathlib import Path

PATTERNS = (
    re.compile(rb"ntn_[A-Za-z0-9]{20,}"),
    re.compile(rb"gsk_[A-Za-z0-9]{20,}"),
    re.compile(rb"\b[0-9]{8,12}:[A-Za-z0-9_-]{30,}"),
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--staged", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    command = (
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"]
        if args.staged
        else ["git", "ls-files"]
    )
    result = subprocess.run(command, cwd=root, capture_output=True, check=True)
    names = result.stdout.decode().splitlines()
    secrets = []
    env = root / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                if ("TOKEN" in key or "API_KEY" in key) and len(value.strip()) >= 8:
                    secrets.append(value.strip().encode())
    forbidden = []
    for name in names:
        payload = subprocess.run(
            ["git", "show", ":" + name], cwd=root, capture_output=True, check=True
        ).stdout
        if name == ".env" or name.startswith(("state/", ".venv/", ".codegraph/")):
            forbidden.append(name)
        elif any(pattern.search(payload) for pattern in PATTERNS) or any(
            secret in payload for secret in secrets
        ):
            forbidden.append(name)
    if forbidden:
        print("Secret check FAILED; review files: " + ", ".join(forbidden))
        raise SystemExit(1)
    print(f"Secret check passed ({len(names)} staged/tracked files)")


if __name__ == "__main__":
    main()
