# Usage fixtures

Recorded or derived outputs that pin the per-rail usage readers (spec
`docs/specs/2026-10-13-usage-and-budget-design.md` §3.1 and §4).

| File | Origin |
|---|---|
| `claude.no-schema.json` | Recorded 2026-10-13 with Claude Code 2.1.296: one `claude -p --output-format json` call without a JSON schema (model alias `haiku`, one turn, no tools). `session_id` and `uuid` zeroed; nothing else changed. |
| `claude.two-models.synthetic.json` | **Synthetic.** `../structured/claude-2.1.283.success.json` plus a second `modelUsage` entry `claude-sonnet-4-5-20250929` (input 5, output 20, cache read 1000, cache creation 0, no `thinkingTokens`) and `total_cost_usd` 0.020488. A real two-model envelope cannot be forced: claude chooses its side models itself. |

Reused as they are, from earlier recordings:

- codex `--json` stream with one `turn.completed`: `../tool_counts/codex.events.jsonl`;
- agy stream-json output with a final `result` event: `../tool_counts/agy.events.jsonl`;
- claude schema envelopes: `../structured/claude-2.1.283.success.json` and
  `../structured/claude-2.1.283.max-turns.json`;
- opencode stream: `../tool_counts/opencode.events.jsonl`;
- vibe stream without usage: `../vibe/read-ok.events.jsonl`.

No openai_compat response body is recorded here: recording one is a billed OpenRouter
call, kept for the release smoke. Its rail is unchanged by this lot and stays pinned by
`test_provider_openai_compat.py`.
