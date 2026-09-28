.PHONY: test lint format typecheck check

test:
	PYTHONPATH=src python3 -m unittest discover -s tests -v

lint:
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .

format:
	.venv/bin/ruff check --fix .
	.venv/bin/ruff format .

typecheck:
	.venv/bin/mypy

check: lint typecheck test
