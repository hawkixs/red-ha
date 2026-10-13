# Mistral Vibe 2.26.0 stream fixtures

Recorded on 2026-10-09 (s*) and 2026-10-13 (api-error-*) with `vibe -p --output streaming`,
model `mistral-large-latest`, a hardened per-run `VIBE_HOME` (spec
`docs/specs/2026-10-13-vibe-rail-design.md` §3.2). Absolute paths are rewritten to
`/fixture/work` (the `--workdir`) and `/fixture/outside`.

| File | Scenario | Exit |
|---|---|---|
| `read-ok.events.jsonl` | s2: one completed `file_system.read_file`, then the final answer | 0 |
| `write-inside-outside.events.jsonl` | s4: a completed write inside the workdir, a write outside denied `tool_denied` after a `callback` | 0 |
| `sigint-bye.stdout` | s9: SIGINT after 6 s; stdout ends with a `Bye!` line, not JSON | 0 |
| `turn-limit.events.jsonl` | s12: `--max-turns 2` on a longer task; no final assistant message | 0 |
| `denied-by-policy.events.jsonl` | s16: write, bash and code-mode write attempts with only `read_file` enabled, all denied `tool_denied` | 0 |
| `api-error-invalid-key.events.jsonl` / `.stderr` | invalid `MISTRAL_API_KEY`: stdout holds only the user message entry, error on stderr | 1 |
| `api-error-invalid-model.events.jsonl` / `.stderr` | unknown model id: stdout holds only the user message entry, HTTP 400 `invalid_model` on stderr | 1 |
