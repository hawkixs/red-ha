# red-ha task runner. CI runs the commands in `ci` in this order.

.PHONY: sync lint test check ci

RAIL_FLAGS ?=

sync:
	uv sync

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests

test:
	uv run pytest tests/unit -q

check:
	rail check $(RAIL_FLAGS)

ci:
	uv run ruff check src tests
	uv run ruff format --check src tests
	uv run mypy src
	uv run pytest tests/unit -q
	uv build
