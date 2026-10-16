# Worker proof timer implementation plan

Goal: detect worker CLI changes every fifteen minutes and re-prove stale proofs without a manual step.

Architecture: a small installed `ha-reprove-workers` entry point calls the installed sibling `ha providers --json` and then `ha prove RAIL --stale --json` for installed workers. Only `ha prove` owns proof records and trust. A systemd user timer schedules the entry point; each attempted proof appends `PROVE RAIL VERSION passed|failed` to the coordination events log. Unchanged, proven workers produce no events. No vendor updater or proof semantics changes.

Constraints: DIRECT method; strict TDD; separate PR; exact-head CI, rail and independent non-builder judgment before merge; announce merge SHA before host installation. Default workers are codex, agy and opencode. Default cadence fifteen minutes. Missing workers are skipped; command/protocol failures are visible and fail the service. Use an exclusive cycle lock to prevent overlapping manual/timer runs. Existing failed proofs may be retried next cycle.

- [x] Add subprocess tests using a fake installed ha executable: stale success, failed proof, quiet fresh cycle, unavailable worker, malformed output, command failure, and lock contention. Confirm failure before implementation.
- [x] Implement `src/headless_agents/maintenance.py` and installed entry point with bounded commands, per-worker failure isolation, append-only events, and no proof writes.
- [x] Add user service/timer templates plus install/disable instructions and changelog. Validate units with systemd-analyze.
- [ ] Run focused tests, make ci and rail check; inspect full diff; publish PR and exact-head independent DeepSeek review, CI and rail review.
- [ ] Merge with match-head-commit, report SHA on coordination ticket, install merged entry point and units, verify timer and a real maintenance cycle, report evidence.
