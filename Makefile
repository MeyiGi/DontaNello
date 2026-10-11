.PHONY: test lint format typecheck architecture deadcode complexity diagnostics check

test:
	PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v

lint:
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .

format:
	.venv/bin/ruff check --fix .
	.venv/bin/ruff format .

typecheck:
	.venv/bin/mypy

architecture:
	PYTHONPATH=src .venv/bin/lint-imports --no-cache

deadcode:
	.venv/bin/python scripts/quality_diagnostics.py deadcode

complexity:
	.venv/bin/python scripts/quality_diagnostics.py complexity

diagnostics: deadcode complexity

check: lint typecheck architecture test deadcode
