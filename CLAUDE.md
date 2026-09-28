# red-HA — ReD project

## Project

`headless-agents` is a Python library and the `ha` CLI for running headless provider tasks under caller supplied capability profiles. It supports Claude, Codex, agy, OpenCode, and OpenAI compatible HTTP providers. Callers choose credentials, workspace access, tools, and review or proof workflows; the package enforces those choices and records run results.

- Public repository: `https://github.com/hawkixs/red-ha`
- Brain project key: `red-ha`
- Package: `headless-agents`, Python 3.12+, source in `src/headless_agents`
- ReD rail: `tier: bootstrap`, `ledger: file` in `rail.yaml`
- Stage 1 contract receipt: `docs/receipts/`; stage 2 design spec belongs in `docs/specs/`

## Commands

```sh
uv sync
make ci
uv run pytest tests/unit -q
rail check
```

`make ci` runs Ruff lint, Ruff format check, mypy, unit tests, and a package build. `rail check` is the rail verdict. Use strict TDD for every behavior change: write a failing test, make it pass, then refactor while tests stay green.

Live tests are a deliberate release proof and spend real provider quota. They never run in CI:

```sh
HA_LIVE=1 uv run pytest -m live tests/live
```

## Delivery and releases

Every change goes through a pull request with an independent review. Write all GitHub content in English. The repository is public: keep private hostnames, keys, and internal paths out of committed files, issues, and pull requests. File ledger receipts are append only and written by the rail; do not edit them by hand.

Release tags use `vX.Y.Z` (`v0.5.4` is next). After installing a release, replay `ha prove --stale`, the G1 live test in `tests/live/headless_agents/test_concurrency_live.py`, and this end-to-end checklist: confirm `ha --version` matches the tag, run a documented `ha run` flow against an installed provider, inspect its result with `ha show`, and verify the recorded proof state. Record the results as release evidence before declaring the release complete.
