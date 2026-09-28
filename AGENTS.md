# red-HA — repository guidance

Read `CLAUDE.md` for the project, commands, release process, and public repository rules. The Brain project key is `red-ha`.

`ha` is the `headless-agents` package's CLI. The library runs headless provider tasks with explicit capability profiles and records results and proofs. Its runtime code lives in `src/headless_agents`; unit and live tests live in `tests/unit/headless_agents` and `tests/live/headless_agents`.

## Work rules

- Use strict TDD for behavior changes: failing test, minimal implementation, passing test, then refactor.
- Run `uv sync`, `make ci`, and `rail check` before opening a pull request. Every change needs a pull request and an independent review.
- The rail is `tier: bootstrap` with `ledger: file`. Keep the stage 1 contract receipt in `docs/receipts/`; put the stage 2 design spec in `docs/specs/`. Do not edit receipts by hand.
- Run unit tests with `uv run pytest tests/unit -q`. Live tests use `HA_LIVE=1 uv run pytest -m live tests/live`; they spend provider quota, prove release behavior, and never run in CI.
- Tag releases `vX.Y.Z`. After installing each release, replay `ha prove --stale`, the G1 live test, and the end-to-end checklist in `CLAUDE.md`.
- Everything on GitHub is in English. This is a public repository: keep private hostnames, keys, and internal paths out of it.
