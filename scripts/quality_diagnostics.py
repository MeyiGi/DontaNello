"""Read-only diagnostics; findings are review candidates, never quality gates."""

import argparse
import ast
from pathlib import Path


def deadcode(source: Path, limit: int) -> None:
    from vulture import Vulture

    scanner = Vulture()
    scanner.scavenge([str(source), "tests"])
    if scanner.exit_code:
        raise SystemExit(scanner.exit_code)
    # Ruff already owns unused imports/locals. Inspect larger structural candidates here.
    findings = [
        item
        for item in scanner.get_unused_code(min_confidence=60, sort_by_size=True)
        if item.typ in {"class", "function", "method", "property", "unreachable_code"}
        and item.filename.is_relative_to(source.resolve())
    ]
    print(f"Vulture: {len(findings)} potential candidates; no automatic deletion or failure.")
    for item in findings[:limit]:
        print(item.get_report(add_size=True))


def complexity(source: Path, limit: int) -> None:
    from cognitive_complexity.api import get_cognitive_complexity

    findings = []
    for path in sorted(source.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                findings.append((get_cognitive_complexity(node), str(path), node.lineno, node.name))
    print("Cognitive complexity: diagnostic approximation; no threshold or pass/fail grade.")
    for score, path, line, name in sorted(findings, reverse=True)[:limit]:
        print(f"{path}:{line}: {name}: {score}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("deadcode", "complexity"))
    parser.add_argument("--source", type=Path, default=Path("src"))
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    if not args.source.is_dir() or args.limit < 1:
        parser.error("source must be a directory and limit must be positive")
    {"deadcode": deadcode, "complexity": complexity}[args.kind](args.source, args.limit)
