# red-ha — Bootstrap design

- **Date**: 2026-09-28
- **Status**: bootstrap, written by `rail new` — replace it with the real design before
  leaving tier `bootstrap`

## 1. Problem

Python library and ha CLI to run tasks on headless agent providers

## 2. Decisions

| # | Decision |
|---|---|
| 1 | Tier `bootstrap`, stack `python`, ledger `file` (`rail.yaml`) |
| 2 | One remote, GitHub `hawkixs/red-ha` — ReD is GitHub only; a mirror is a declaration |

## 3. Non-goals

Nothing beyond the bootstrap: no feature is designed here.

## 4. Success criteria

`rail check` passes at tier `bootstrap` on a fresh clone.
