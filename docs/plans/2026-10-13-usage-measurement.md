# Usage on every rail, PR1 (measurement) — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** claude, codex and agy runs record the tokens (and, on claude, the cost) their CLI
reports; a chain's earlier links count in the run; `run.json` carries run token totals with
a completeness flag; one summary line closes every `ha run`; `ha show` prints the totals.
This is PR1 (ticket G5 / 82f066ba) of the spec; caps are PR2.

**Architecture:** `envelope.py` gains pure extraction steps (`extract_claude`,
`_codex_turn_usage`, `_agy_result`) that the public `unwrap_*` helpers and the rails share,
plus the rails' own aggregations (`claude_usage`, `codex_usage`, `agy_usage`); the public
results stay byte-for-byte. A new `usage.py` holds `StreamMark`, the byte offset of a stream
recorded when the child is launched and `None` when nothing launched. claude prints
`--output-format json` on every run: its stdout goes to `<answer_log|raw_log>.envelope.json`,
the answer is the envelope's `result`. `report.py` adds `attempts` to each step, run totals
and `summary_line`; the engine collects the earlier links of a chain and says the summary
once; `show.py` renders a `total` line.

**Tech Stack:** Python 3.12.7+, uv, pytest, ruff (line length 100, `ruff format`), mypy
(`disallow_untyped_defs` on `src`); no new dependency.

**Spec:** `docs/specs/2026-10-13-usage-and-budget-design.md` (approved; PR1 = §3.1, §3.2,
§3.3 without `budget`, and the PR1 half of §4 — read them first).

## Global Constraints

- Each rail measures only the bytes its child wrote in this invocation; a rail that returns
  before launching (refusal, launch failure, an already expired deadline) records
  `tokens=None` without reading any file. Measuring never changes an exit code or a failure
  classification (spec §3.1).
- claude: the argv always carries `--output-format json`; `--json-schema` only when a schema
  is asked. Without a schema, stdout goes to `<answer_log>.envelope.json` (or
  `<raw_log>.envelope.json` when only `raw_log` is passed), truncated when the child starts;
  an envelope path equal to `raw_log` or `answer_log` is a usage error (`ValueError`). The
  answer is the envelope's `result` string, written to `answer_log`, or to
  `<raw_log>.answer.txt` (then `report_path`) for a raw-log-only caller; stdout that is not a
  JSON object is the answer verbatim and measures nothing. stderr keeps its destination
  (`raw_log`). Schema runs keep `claude-result.json` (`RESULT_ENVELOPE_NAME`) untouched.
- claude normalisation over **all** models of `modelUsage`: `input` = inputTokens +
  cacheReadInputTokens + cacheCreationInputTokens; `cached` = cacheReadInputTokens;
  `fresh` = `input - cached`; `output` = outputTokens; `thinking` = sum of `thinkingTokens`
  where reported, `None` when no model reports it, never added to `output`; `cost_usd` =
  `total_cost_usd`.
- codex: the single `turn.completed` of the `--json` stream: `input`, `cached` =
  `cached_input_tokens`, `fresh = input - cached`, `output`, `thinking` =
  `reasoning_output_tokens`; more than one `turn.completed` → `tokens=None`; `cost_usd`
  stays `None` (D3, no price table anywhere).
- agy: the final `result` event of the stream-json output. agy's `input_tokens` EXCLUDES
  cache reads (measured on 15 real result events: `total_tokens = input_tokens +
  output_tokens` every time, `cache_read_tokens` up to 15x `input_tokens`), so the rail
  normalises like the other rails (spec amendment A3): `input` = `input_tokens` +
  `cache_read_tokens`, `cached` = `cache_read_tokens`, `fresh` = `input_tokens`, `output`
  = `output_tokens` (thinking included, as agy counts it), `thinking` = `thinking_tokens`;
  `cost_usd` stays `None`. The public `unwrap_agy` keeps its current result.
- opencode and openai_compat unchanged (the reference); vibe unchanged, `None`.
- `envelope.unwrap_*` public results unchanged (spec §5 non-goal).
- `RUN_KEYS` gains `tokens` and `tokens_complete`; `STEP_KEYS` gains **only** `attempts`
  (one entry per earlier link: `{provider, model, exit_code, tokens, cost_usd}`). No
  `budget`, `partial_answer`, `steps_after_crossing`, `started` or `failure_reason` step key
  in this PR. Schema version stays 1; older reports read the new keys as absent (`null`,
  `[]` for `attempts`).
- Run totals: per `TokenUsage` field, the sum over every step and every attempt that
  measured it, `null` when none did; `tokens_complete` true when every step and attempt
  carries `tokens`; `cost_usd`/`cost_complete` keep their meaning and include attempts.
- One run summary line on stderr after all accounting, e.g.
  `ha: run 20261013T…: exit 0, cost 0.37 USD, input 8.3M (cached 7.4M), output 5.8k`; an
  unmeasured figure is omitted, never `0`; an incomplete one is marked
  (`cost ≥ 0.37 USD (incomplete)`).
- Strict TDD, unit tests only, no provider and no network. `make ci` green at every commit.
- English everywhere; public repository: no private hostnames, paths or ids in code, tests
  or fixtures.

## Review Focus

- **A reused log after a run that never launched** (codex and agy: refusal, launch
  failure, expired deadline; claude: no credentials): an earlier run's stream or envelope
  must never be measured as this run's — pinned in Task 2 (codex), Task 3 (agy) and Task 5
  (claude).
- **A claude stdout that is a JSON object without `result`** (the recorded max-turns
  envelope) and **a stdout that is not JSON at all**: the first still measures with no
  answer and the same exit code, the second is the verbatim answer with no usage — pinned
  in Task 4 and Task 5.
- **A codex stream with two `turn.completed`, or a failed turn after a completed one**:
  not measured / measured, exit classification unchanged — pinned in Task 1 and Task 2.
- **An older `run.json` or step without the new keys** (no `tokens_complete`, no
  `attempts`): totals still computed, `ha show` prints no bogus total line — pinned in
  Task 6 and Task 8.
- **An envelope path that aliases a caller's log** (`raw_log` named
  `<answer_log>.envelope.json`): refused before any file is touched or process started —
  pinned in Task 4.

---

## File structure

| File | Responsibility |
|---|---|
| `tests/unit/headless_agents/fixtures/usage/` (create, Task 0) | recorded and labelled-synthetic claude envelopes, provenance README |
| `src/headless_agents/envelope.py` (modify) | shared extraction steps, rail aggregations |
| `src/headless_agents/usage.py` (create) | `StreamMark` |
| `src/headless_agents/providers/codex.py`, `agy.py` (modify) | launch mark, tokens on the result |
| `src/headless_agents/providers/claude.py` (modify) | JSON on every run, envelope and answer files, tokens and cost |
| `src/headless_agents/report.py` (modify) | `attempts`, run totals, `summary_line` |
| `src/headless_agents/engine.py` (modify) | collect attempts, say the summary once |
| `src/headless_agents/show.py` (modify) | `total` line |
| `src/headless_agents/spec.py`, `README.md`, `CHANGELOG.md` (modify) | documentation |

---

### Task 0: Record the missing fixtures (orchestrating session, not the worker)

The worker has no network and must not run a provider; this task is done by the session
that orchestrates, before Task 1 is dispatched. Already recorded and reused as they are
(real runs, structure verbatim, free text redacted — `test_tool_counts.py:1-4`):

- codex `--json` stream with exactly one `turn.completed`:
  `tests/unit/headless_agents/fixtures/tool_counts/codex.events.jsonl` (input 108250,
  cached 88832, output 2373, reasoning 1814);
- agy stream-json output with a final `result` event:
  `tests/unit/headless_agents/fixtures/tool_counts/agy.events.jsonl` (input 133605, output
  9307, thinking 5197, cache_read 634901);
- claude schema envelopes: `fixtures/structured/claude-2.1.283.success.json` (cache
  creation, thinkingTokens 43, line 49) and `claude-2.1.283.max-turns.json` (cache read and
  creation, no `result`, cost and modelUsage present);
- opencode stream: `fixtures/tool_counts/opencode.events.jsonl`; vibe stream with no usage:
  `fixtures/vibe/read-ok.events.jsonl` (already pinned `tokens is None`,
  `test_provider_vibe.py:815`).

Missing, recorded or built here, with these exact names (later tasks reference them):

**Files:**
- Create: `tests/unit/headless_agents/fixtures/usage/claude.no-schema.json` (real)
- Create: `tests/unit/headless_agents/fixtures/usage/claude.two-models.synthetic.json` (synthetic)
- Create: `tests/unit/headless_agents/fixtures/usage/README.md`

**Interfaces:**
- Consumes: the installed `claude` CLI (subscription; spec D10 says subscription rails are
  free to use).
- Produces: the three files above.

- [ ] **Step 1: Record the non-schema envelope** (one short call, isolated from the
  operator's configuration as ha runs it):

```sh
cd <worktree>
tmp=$(mktemp -d)
printf 'Reply with the single word: ok' | claude -p - --model claude-haiku-4-5 \
  --max-turns 1 --output-format json --permission-mode bypassPermissions --tools "" \
  > "$tmp/envelope.json"
claude --version
```

- [ ] **Step 2: Scrub it** — ids to zeros, nothing else changed, pretty-printed like the
  `structured/` fixtures:

```sh
uv run python - "$tmp/envelope.json" <<'PY'
import json, pathlib, sys
doc = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
for key in ("session_id", "uuid"):
    if key in doc:
        doc[key] = "00000000-0000-0000-0000-000000000000"
assert isinstance(doc.get("result"), str) and doc["result"].strip(), "no result string"
assert "structured_output" not in doc, "a schema envelope, not a plain one"
assert isinstance(doc.get("total_cost_usd"), (int, float)), "no cost"
assert doc.get("modelUsage"), "no modelUsage"
out = pathlib.Path("tests/unit/headless_agents/fixtures/usage/claude.no-schema.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
PY
grep -nE '/home/|/Users/|hawixs|@' tests/unit/headless_agents/fixtures/usage/claude.no-schema.json
```

Expected: the python script exits 0; `grep` prints nothing (exit 1). If a path or an
account id appears, replace it with `<redacted>` by hand and rerun the `grep`.

- [ ] **Step 3: Build the two-model envelope.** A real one cannot be forced: which side
  model claude calls is claude's choice. It is therefore synthetic, derived from the real
  `success.json` by adding a second `modelUsage` entry with fixed numbers (the tests pin
  them):

```sh
uv run python - <<'PY'
import json, pathlib
src = pathlib.Path("tests/unit/headless_agents/fixtures/structured/claude-2.1.283.success.json")
doc = json.loads(src.read_text(encoding="utf-8"))
doc["modelUsage"]["claude-sonnet-4-5-20250929"] = {
    "inputTokens": 5, "outputTokens": 20, "cacheReadInputTokens": 1000,
    "cacheCreationInputTokens": 0, "webSearchRequests": 0, "costUSD": 0.001,
    "contextWindow": 200000, "maxOutputTokens": 32000,
    "canonicalModel": "claude-sonnet-4-5", "provider": "firstParty", "costBasis": "list",
}
doc["total_cost_usd"] = 0.020488
out = pathlib.Path("tests/unit/headless_agents/fixtures/usage/claude.two-models.synthetic.json")
out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
PY
```

- [ ] **Step 4: Write `fixtures/usage/README.md`** stating, per file: `claude.no-schema.json`
  — recorded on <date> with claude <version from Step 1>, `session_id`/`uuid` zeroed;
  `claude.two-models.synthetic.json` — **synthetic**, `structured/claude-2.1.283.success.json`
  plus a second `modelUsage` entry `claude-sonnet-4-5-20250929` (input 5, output 20, cache
  read 1000, cache creation 0, no thinkingTokens) and `total_cost_usd` 0.020488; and the
  list of reused fixtures above. No openai_compat body: recording one is an
  OpenRouter-billed call, which the operator reserves for the release smoke (rule ca12005a);
  its existing pins stay `test_provider_openai_compat.py:82-131`.

- [ ] **Step 5: Verify** — `uv run python -c "import json,sys; [json.load(open(p)) for p in sys.argv[1:]]" tests/unit/headless_agents/fixtures/usage/*.json` exits 0.

- [ ] **Step 6: Commit**

```sh
git add tests/unit/headless_agents/fixtures/usage/claude.no-schema.json
git add tests/unit/headless_agents/fixtures/usage/claude.two-models.synthetic.json
git add tests/unit/headless_agents/fixtures/usage/README.md
git commit -m "🧪 test(usage): record the claude envelopes the usage readers are pinned on"
```

---

### Task 1: Shared extraction and the rails' usage readers in `envelope.py`

**Files:**
- Modify: `src/headless_agents/envelope.py:90-201` (claude, codex and agy sections)
- Test: `tests/unit/headless_agents/test_usage_readers.py` (create)

**Interfaces:**
- Consumes: `TokenUsage` (`result.py:32-40`), the Task 0 fixtures.
- Produces:
  - `@dataclass(frozen=True, kw_only=True) class ClaudeEnvelope` with `text: str | None`, `cost_usd: float | None`, `models: tuple[tuple[str, Mapping[str, object]], ...]`, `usage: Mapping[str, object]`
  - `extract_claude(stdout: str) -> ClaudeEnvelope | None` (`None` when stdout is not a non-empty JSON object)
  - `claude_usage(envelope: ClaudeEnvelope) -> TokenUsage | None`
  - `codex_usage(stdout: str) -> TokenUsage | None`
  - `agy_usage(stdout: str) -> TokenUsage | None`
  - `unwrap_claude`, `unwrap_codex`, `unwrap_agy`, `unwrap`: signatures and results unchanged

- [ ] **Step 1: Write the failing tests** — create `tests/unit/headless_agents/test_usage_readers.py`:

```python
"""The rails' usage readers (spec 2026-10-13 usage and budget §3.1, §4 PR1).

Each reader is pinned on a recorded output; the public ``unwrap_*`` helpers are pinned
unchanged on the same fixtures (spec §5: their results do not move).
"""

from __future__ import annotations

import json
from pathlib import Path

from headless_agents.envelope import (
    Reply,
    agy_usage,
    claude_usage,
    codex_usage,
    extract_claude,
    unwrap_agy,
    unwrap_claude,
    unwrap_codex,
)
from headless_agents.providers import opencode
from headless_agents.result import TokenUsage

FIXTURES = Path(__file__).parent / "fixtures"
STRUCTURED = FIXTURES / "structured"
USAGE = FIXTURES / "usage"
TOOL_COUNTS = FIXTURES / "tool_counts"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestClaudeUsage:
    def test_the_success_envelope_sums_cache_creation_into_input(self) -> None:
        envelope = extract_claude(_read(STRUCTURED / "claude-2.1.283.success.json"))
        assert envelope is not None
        assert envelope.text == '{"ok":true}'
        assert envelope.cost_usd == 0.019488
        assert claude_usage(envelope) == TokenUsage(
            input=9499, output=100, fresh=9499, cached=0, thinking=43
        )

    def test_the_max_turns_envelope_measures_without_a_result(self) -> None:
        envelope = extract_claude(_read(STRUCTURED / "claude-2.1.283.max-turns.json"))
        assert envelope is not None
        assert envelope.text is None
        assert envelope.cost_usd == 0.025328600000000003
        assert claude_usage(envelope) == TokenUsage(
            input=30004, output=531, fresh=10368, cached=19636, thinking=350
        )

    def test_two_models_are_summed_and_thinking_counts_where_reported(self) -> None:
        envelope = extract_claude(_read(USAGE / "claude.two-models.synthetic.json"))
        assert envelope is not None
        assert envelope.cost_usd == 0.020488
        assert claude_usage(envelope) == TokenUsage(
            input=10504, output=120, fresh=9504, cached=1000, thinking=43
        )

    def test_thinking_is_none_when_no_model_reports_it(self) -> None:
        single = json.loads(_read(STRUCTURED / "claude-2.1.283.max-turns.json"))
        for detail in single["modelUsage"].values():
            del detail["thinkingTokens"]
        envelope = extract_claude(json.dumps(single))
        assert envelope is not None
        usage = claude_usage(envelope)
        assert usage is not None and usage.thinking is None and usage.output == 531
        double = json.loads(_read(USAGE / "claude.two-models.synthetic.json"))
        for detail in double["modelUsage"].values():
            detail.pop("thinkingTokens", None)
        envelope = extract_claude(json.dumps(double))
        assert envelope is not None
        usage = claude_usage(envelope)
        assert usage is not None and usage.thinking is None and usage.output == 120

    def test_the_recorded_envelope_without_a_schema(self) -> None:
        raw = _read(USAGE / "claude.no-schema.json")
        document = json.loads(raw)
        models = list(document["modelUsage"].values())
        read = sum(m["cacheReadInputTokens"] for m in models)
        given = sum(
            m["inputTokens"] + m["cacheReadInputTokens"] + m["cacheCreationInputTokens"]
            for m in models
        )
        thinking = [m["thinkingTokens"] for m in models if "thinkingTokens" in m]
        envelope = extract_claude(raw)
        assert envelope is not None
        assert "structured_output" not in document
        assert envelope.text == document["result"]
        assert envelope.cost_usd == document["total_cost_usd"]
        assert given > 0
        assert claude_usage(envelope) == TokenUsage(
            input=given,
            output=sum(m["outputTokens"] for m in models),
            fresh=given - read,
            cached=read,
            thinking=sum(thinking) if thinking else None,
        )

    def test_what_is_not_an_envelope_extracts_nothing(self) -> None:
        assert extract_claude("plain text, not json") is None
        assert extract_claude("[1, 2]") is None
        assert extract_claude("") is None
        empty = extract_claude("{}")
        assert empty is not None and empty.text is None and empty.cost_usd is None
        assert claude_usage(empty) is None
        assert unwrap_claude("{}") == Reply(text="{}")
        envelope = extract_claude('{"result": "x"}')
        assert envelope is not None and envelope.text == "x"
        assert claude_usage(envelope) is None

    def test_the_public_unwrap_is_unchanged_on_the_fixtures(self) -> None:
        success = _read(STRUCTURED / "claude-2.1.283.success.json")
        assert unwrap_claude(success) == Reply(
            text='{"ok":true}',
            model_reported="claude-haiku-4-5-20251001",
            tokens=TokenUsage(input=10, output=100, cached=0),
            cost_usd=0.019488,
        )
        max_turns = _read(STRUCTURED / "claude-2.1.283.max-turns.json")
        assert unwrap_claude(max_turns) == Reply(
            text=max_turns,
            model_reported="claude-haiku-4-5-20251001",
            tokens=TokenUsage(input=26, output=531, cached=19636),
            cost_usd=0.025328600000000003,
        )
        two = _read(USAGE / "claude.two-models.synthetic.json")
        assert unwrap_claude(two) == Reply(
            text='{"ok":true}', tokens=TokenUsage(input=10, output=100), cost_usd=0.020488
        )


RECORDED_CODEX = TokenUsage(input=108250, output=2373, fresh=19418, cached=88832, thinking=1814)


class TestCodexUsage:
    def test_the_recorded_stream(self) -> None:
        stream = _read(TOOL_COUNTS / "codex.events.jsonl")
        assert codex_usage(stream) == RECORDED_CODEX
        assert unwrap_codex(stream) == Reply(text="<redacted>", tokens=RECORDED_CODEX)

    def test_two_completed_turns_are_not_measured(self) -> None:
        second = {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 1,
                "cached_input_tokens": 0,
                "output_tokens": 1,
                "reasoning_output_tokens": 0,
            },
        }
        stream = _read(TOOL_COUNTS / "codex.events.jsonl") + json.dumps(second) + "\n"
        assert codex_usage(stream) is None
        # The public helper keeps its own rule: the last turn wins.
        assert unwrap_codex(stream).tokens == TokenUsage(
            input=1, output=1, fresh=1, cached=0, thinking=0
        )

    def test_a_failed_turn_after_the_completed_one_keeps_its_usage(self) -> None:
        failed = {"type": "turn.failed", "error": {"message": "boom"}}
        stream = _read(TOOL_COUNTS / "codex.events.jsonl") + json.dumps(failed) + "\n"
        assert codex_usage(stream) == RECORDED_CODEX

    def test_no_completed_turn_or_an_empty_usage_is_not_measured(self) -> None:
        assert codex_usage("") is None
        assert codex_usage('{"type": "turn.started"}\n') is None
        assert codex_usage('{"type": "turn.completed"}\n') is None
        assert codex_usage('{"type": "turn.completed", "usage": {}}\n') is None


RECORDED_AGY = TokenUsage(input=133605, output=9307, cached=634901, thinking=5197)
# The rail's normalisation (spec A3): agy's input_tokens excludes cache reads.
RECORDED_AGY_RAIL = TokenUsage(
    input=768506, output=9307, fresh=133605, cached=634901, thinking=5197
)


class TestAgyUsage:
    def test_the_recorded_stream(self) -> None:
        stream = _read(TOOL_COUNTS / "agy.events.jsonl")
        assert agy_usage(stream) == RECORDED_AGY_RAIL
        assert unwrap_agy(stream) == Reply(text="<redacted>", tokens=RECORDED_AGY)

    def test_input_adds_cache_reads_only_when_both_are_measured(self) -> None:
        line = '{"event": "result", "result": {"usage": {"input_tokens": 7}}}\n'
        assert agy_usage(line) == TokenUsage(input=None, fresh=7)

    def test_a_stream_without_a_measured_result_is_not_measured(self) -> None:
        assert agy_usage("") is None
        assert agy_usage('{"event": "init"}\n') is None
        assert agy_usage('{"event": "result", "result": {"response": "x"}}\n') is None


class TestReferenceRails:
    def test_opencode_counts_a_re_emitted_part_once(self, tmp_path: Path) -> None:
        recorded = _read(TOOL_COUNTS / "opencode.events.jsonl")
        once = tmp_path / "once.jsonl"
        once.write_text(recorded, encoding="utf-8")
        last = [line for line in recorded.splitlines() if '"step_finish"' in line][-1]
        twice = tmp_path / "twice.jsonl"
        twice.write_text(recorded + last + "\n", encoding="utf-8")
        tokens, cost = opencode.telemetry(once)
        assert opencode.telemetry(twice) == (tokens, cost)
        assert tokens is not None and tokens.input is not None
        assert tokens.fresh is not None and tokens.cached is not None
        assert tokens.input == tokens.fresh + tokens.cached
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/unit/headless_agents/test_usage_readers.py -q`
Expected: FAIL at collection, `ImportError: cannot import name 'agy_usage' from 'headless_agents.envelope'`.

- [ ] **Step 3: Implement** — in `src/headless_agents/envelope.py`, replace everything from
  `def _claude_model_usage` (line 90) to the end of `unwrap_agy` (line 201) with:

```python
def _claude_model_usage(detail: Mapping[str, object]) -> TokenUsage:
    return TokenUsage(
        input=_int(detail, "inputTokens"),
        output=_int(detail, "outputTokens"),
        cached=_int(detail, "cacheReadInputTokens"),
    )


_ModelUsage = tuple[tuple[str, Mapping[str, object]], ...]


@dataclass(frozen=True, kw_only=True)
class ClaudeEnvelope:
    """What claude's ``--output-format json`` envelope carries, extracted once.

    The shared extraction step (spec 2026-10-13 usage §3.1): :func:`unwrap_claude` and the
    claude rail both read it, each with its own aggregation.
    """

    text: str | None
    cost_usd: float | None
    models: _ModelUsage
    usage: Mapping[str, object]


def extract_claude(stdout: str) -> ClaudeEnvelope | None:
    """The envelope in ``stdout``; ``None`` when stdout is not a JSON object.

    ``{}`` IS an envelope -- one without a result, a cost or a usage: the claude rail
    then answers nothing (spec 2026-10-13 usage §3.1), and :func:`unwrap_claude` still
    returns it verbatim, as it always did.
    """
    data = _load_object(stdout)
    if data is None:
        return None
    return ClaudeEnvelope(
        text=_text(data, "result"),
        cost_usd=_number(data, "total_cost_usd"),
        models=tuple(
            (model, detail)
            for model, raw in _mapping(data.get("modelUsage")).items()
            if isinstance(model, str) and (detail := _mapping(raw))
        ),
        usage=_mapping(data.get("usage")),
    )


def _total(models: _ModelUsage, key: str) -> int | None:
    values = [_int(detail, key) for _, detail in models]
    if not values or any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def claude_usage(envelope: ClaudeEnvelope) -> TokenUsage | None:
    """The claude rail's aggregation: the whole invocation, every model summed.

    ``input`` counts cache reads and cache creation; ``fresh`` is ``input - cached`` (cache
    creation is fresh input); ``thinking`` sums ``thinkingTokens`` where reported and is
    never added to ``output``. ``None`` without ``modelUsage``.
    """
    models = envelope.models
    if not models:
        return None
    given = _total(models, "inputTokens")
    read = _total(models, "cacheReadInputTokens")
    created = _total(models, "cacheCreationInputTokens")
    total_input = (
        given + read + created
        if given is not None and read is not None and created is not None
        else None
    )
    reported = [
        value for _, detail in models if (value := _int(detail, "thinkingTokens")) is not None
    ]
    return TokenUsage(
        input=total_input,
        output=_total(models, "outputTokens"),
        fresh=total_input - read if total_input is not None and read is not None else None,
        cached=read,
        thinking=sum(reported) if reported else None,
    )


def unwrap_claude(stdout: str, *, expected_model: str | None = None) -> Reply:
    """``modelUsage`` is keyed BY the model that played -- the only source of its name.

    ``outputTokens`` is read from ``modelUsage`` and not from
    ``usage.output_tokens``: both exist and differ (4709 against 4692 on the
    reference capture), and it is the ``modelUsage`` one that is attributed to
    the named model. The global ``usage`` is the fallback when no single
    attribution is certain.
    """
    envelope = extract_claude(stdout)
    if envelope is None:
        return Reply(text=stdout)
    text = envelope.text
    cost = envelope.cost_usd
    measured = list(envelope.models)
    chosen = measured if len(measured) == 1 else []
    if expected_model is not None and len(measured) > 1:
        chosen = [
            (model, detail)
            for model, detail in measured
            if model == expected_model or _text(detail, "canonicalModel") == expected_model
        ]
    if len(chosen) == 1:
        model, detail = chosen[0]
        return Reply(
            text=stdout if text is None else text,
            model_reported=model,
            tokens=_claude_model_usage(detail),
            cost_usd=cost,
        )
    usage = envelope.usage
    tokens = (
        TokenUsage(input=_int(usage, "input_tokens"), output=_int(usage, "output_tokens"))
        if usage
        else None
    )
    return Reply(text=stdout if text is None else text, tokens=tokens, cost_usd=cost)


def _codex_turn_usage(usage: Mapping[str, object]) -> TokenUsage | None:
    """One ``turn.completed`` usage block; ``None`` when it is empty."""
    if not usage:
        return None
    input_tokens = _int(usage, "input_tokens")
    cached = _int(usage, "cached_input_tokens")
    fresh = input_tokens - cached if input_tokens is not None and cached is not None else None
    return TokenUsage(
        input=input_tokens,
        output=_int(usage, "output_tokens"),
        fresh=fresh,
        cached=cached,
        thinking=_int(usage, "reasoning_output_tokens"),
    )


def unwrap_codex(stdout: str) -> Reply:
    """JSONL: the text and the consumption live on DIFFERENT lines.

    Neither the first nor the last line will do -- the stream starts with
    ``turn.started`` and ends with ``turn.completed``, which carries no text.
    So we search by type, not by position. ``fresh`` follows the Codex
    convention: ``cached_input_tokens`` is a SUBSET of ``input_tokens``.
    """
    text: str | None = None
    tokens: TokenUsage | None = None
    for event in _jsonl(stdout):
        item = _mapping(event.get("item"))
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            read = _text(item, "text")
            if read is not None:
                text = read
        elif event.get("type") == "turn.completed":
            turn = _codex_turn_usage(_mapping(event.get("usage")))
            if turn is not None:
                tokens = turn
    return Reply(text=stdout if text is None else text, tokens=tokens)


def codex_usage(stdout: str) -> TokenUsage | None:
    """The codex rail's reader: the usage of the ONE ``turn.completed`` of the stream.

    Measured 2026-10-13: ``codex exec`` writes exactly one per run. A stream with more is
    not measured until a recording shows what they mean -- never a guessed sum.
    """
    turns = [event for event in _jsonl(stdout) if event.get("type") == "turn.completed"]
    if len(turns) != 1:
        return None
    return _codex_turn_usage(_mapping(turns[0].get("usage")))


def _agy_usage(usage: Mapping[str, object]) -> TokenUsage | None:
    if not usage:
        return None
    return TokenUsage(
        input=_int(usage, "input_tokens"),
        output=_int(usage, "output_tokens"),
        cached=_int(usage, "cache_read_tokens"),
        thinking=_int(usage, "thinking_tokens"),
    )


def _agy_result(stdout: str) -> Mapping[str, object] | None:
    """The ``result`` of the LAST ``result`` event of a stream-json output, if any."""
    result: Mapping[str, object] | None = None
    for event in _jsonl(stdout):
        if event.get("event") == "result":
            result = _mapping(event.get("result"))
    return result


def unwrap_agy(stdout: str) -> Reply:
    """One object (``--output-format json``) or a stream whose ``result`` event
    carries the same ``response`` and ``usage`` (``stream-json``)."""
    data = _load_object(stdout)
    if data is None:
        data = _agy_result(stdout)
        if data is None:
            return Reply(text=stdout)
    text = _text(data, "response")
    return Reply(
        text=stdout if text is None else text, tokens=_agy_usage(_mapping(data.get("usage")))
    )


def agy_usage(stdout: str) -> TokenUsage | None:
    """The agy rail's reader: the final ``result`` event of its stream-json output.

    agy's ``input_tokens`` EXCLUDES cache reads (``total_tokens`` is input plus
    output on every recorded run), so ``input`` is their sum, like the other rails.
    """
    result = _agy_result(stdout)
    if result is None:
        return None
    usage = _mapping(result.get("usage"))
    if not usage:
        return None
    fresh = _int(usage, "input_tokens")
    cached = _int(usage, "cache_read_tokens")
    return TokenUsage(
        input=fresh + cached if fresh is not None and cached is not None else None,
        output=_int(usage, "output_tokens"),
        fresh=fresh,
        cached=cached,
        thinking=_int(usage, "thinking_tokens"),
    )
```

- [ ] **Step 4: Run the new tests and the public pins**

Run: `uv run pytest tests/unit/headless_agents/test_usage_readers.py tests/unit/headless_agents/test_envelope.py -q`
Expected: all pass (`test_envelope.py` unchanged and green).

- [ ] **Step 5: Lint and type-check** — `uv run ruff format src tests && uv run ruff check src tests && uv run mypy src` exit 0.

- [ ] **Step 6: Commit**

```sh
git add src/headless_agents/envelope.py
git add tests/unit/headless_agents/test_usage_readers.py
git commit -m "✨ feat(envelope): share the extraction and add the rails' usage readers"
```

---

### Task 2: `StreamMark` and codex measurement

**Files:**
- Create: `src/headless_agents/usage.py`
- Modify: `src/headless_agents/providers/codex.py` — `run_codex` (1093-1408: signature, and the
  `with (events_log.open("w") ...)` block of its inner `_run`, 1233-1255) and
  `CodexProvider._run_spec` (1504-1565, the `tokens=None` at 1554)
- Test: `tests/unit/headless_agents/test_usage.py` (create),
  `tests/unit/headless_agents/test_provider_codex.py` (append at the end; reuses its
  `_FakeProcess` 489-534, `_server` 34-45 and the autouse `_operator_codex_login` 47-60)

**Interfaces:**
- Consumes: `codex_usage` (Task 1).
- Produces:
  - `@dataclass class StreamMark` with `offset: int | None = None` and `read(self, path: Path | None) -> str | None`
  - `run_codex(..., launch: StreamMark | None = None) -> int` (new keyword, default keeps every caller unchanged)
  - `CodexProvider.run(spec).tokens` = `codex_usage` of this invocation's bytes; `cost_usd` stays `None`

- [ ] **Step 1: Write the failing tests** — create `tests/unit/headless_agents/test_usage.py`:

```python
"""``StreamMark``: only this invocation's bytes are read (spec 2026-10-13 usage §3.1)."""

from __future__ import annotations

from pathlib import Path

from headless_agents.usage import StreamMark


def test_an_unlaunched_mark_reads_nothing(tmp_path: Path) -> None:
    log = tmp_path / "events.jsonl"
    log.write_text("EARLIER RUN\n", encoding="utf-8")
    assert StreamMark().read(log) is None


def test_a_mark_reads_from_its_offset(tmp_path: Path) -> None:
    log = tmp_path / "events.jsonl"
    log.write_text("EARLIER\nTHIS\n", encoding="utf-8")
    assert StreamMark(offset=8).read(log) == "THIS\n"
    assert StreamMark(offset=0).read(log) == "EARLIER\nTHIS\n"


def test_a_missing_file_reads_nothing(tmp_path: Path) -> None:
    assert StreamMark(offset=0).read(tmp_path / "absent") is None
    assert StreamMark(offset=0).read(None) is None
```

Append to `tests/unit/headless_agents/test_provider_codex.py` (and add
`from headless_agents.result import TokenUsage` to its imports):

```python
# ── usage (spec 2026-10-13 usage and budget §3.1) ──────────────────────────

USAGE_TOOL_COUNTS = Path(__file__).parent / "fixtures" / "tool_counts"
RECORDED_USAGE = TokenUsage(input=108250, output=2373, fresh=19418, cached=88832, thinking=1814)


def _recorded_codex_turn() -> str:
    """The recorded stream's thread, turn, final message and turn.completed lines."""
    kept = []
    for line in (USAGE_TOOL_COUNTS / "codex.events.jsonl").read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        item = event.get("item")
        if event["type"] in ("thread.started", "turn.started", "turn.completed") or (
            isinstance(item, dict) and item.get("type") == "agent_message"
        ):
            kept.append(line + "\n")
    return "".join(kept)


def _usage_popen(
    monkeypatch: pytest.MonkeyPatch, *, events: str, code: int = 0, report: str = "R"
) -> dict[str, object]:
    seen: dict[str, object] = {}

    def popen(command: list[str], **kwargs: object) -> _FakeProcess:
        seen["command"] = command
        report_log = Path(command[command.index("--output-last-message") + 1])
        fake = _FakeProcess(returncode=code, events=events, report=report)
        fake.bind(events_stream=kwargs["stdout"], report_log=report_log)
        return fake

    monkeypatch.setattr(codex.subprocess, "Popen", popen)
    monkeypatch.setattr(codex, "terminate_process_group", lambda process: process.kill())
    return seen


def _usage_spec(tmp_path: Path, **overrides: object) -> RunSpec:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    fields: dict[str, object] = {
        "prompt": "P",
        "model": "m",
        "run_dir": tmp_path / "runs" / "r1",
        "profile": CapabilityProfile(workspace=Workspace(path=ws)),
        "environment": {"PATH": "/usr/bin"},
    }
    fields.update(overrides)
    return RunSpec(**fields)  # type: ignore[arg-type]


class TestCodexUsage:
    def test_the_single_completed_turn_is_measured(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _usage_popen(monkeypatch, events=_recorded_codex_turn())
        result = codex.CodexProvider().run(_usage_spec(tmp_path))
        assert result.exit_code == 0
        assert result.tokens == RECORDED_USAGE
        assert result.cost_usd is None
        written = json.loads((tmp_path / "runs" / "r1" / "result.json").read_text("utf-8"))
        assert written["tokens"] == {
            "input": 108250,
            "output": 2373,
            "fresh": 19418,
            "cached": 88832,
            "thinking": 1814,
        }

    def test_two_completed_turns_are_not_measured(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        events = _recorded_codex_turn() + _events(_turn_completed())
        _usage_popen(monkeypatch, events=events)
        result = codex.CodexProvider().run(_usage_spec(tmp_path))
        assert result.exit_code == 0
        assert result.tokens is None

    def test_a_failed_turn_after_a_completed_one_keeps_the_usage_and_the_failure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        events = _recorded_codex_turn() + _events(
            {"type": "turn.failed", "error": {"message": "boom"}}
        )
        _usage_popen(monkeypatch, events=events, code=1, report="")
        result = codex.CodexProvider().run(_usage_spec(tmp_path))
        assert result.exit_code == PROVIDER_FALLBACK_EXIT_CODE
        assert result.text is None
        assert result.tokens == RECORDED_USAGE

    def _stale(self, tmp_path: Path) -> Path:
        run_dir = tmp_path / "runs" / "r1"
        run_dir.mkdir(parents=True, exist_ok=True)
        events_log = run_dir / "events.jsonl"
        events_log.write_text(_recorded_codex_turn(), encoding="utf-8")
        return events_log

    def test_a_refusal_reads_no_reused_log(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        events_log = self._stale(tmp_path)
        seen = _usage_popen(monkeypatch, events=_recorded_codex_turn())
        ws = tmp_path / "ws"
        ws.mkdir()
        spec = _usage_spec(
            tmp_path, profile=CapabilityProfile(mcp=_server(), workspace=Workspace(path=ws))
        )
        result = codex.CodexProvider().run(spec)
        assert result.exit_code == 1, "the bearer variable is missing: refused as today"
        assert "command" not in seen
        assert result.tokens is None
        assert events_log.read_text(encoding="utf-8") == _recorded_codex_turn()

    def test_a_launch_failure_reads_no_reused_log(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._stale(tmp_path)

        def popen(command: list[str], **kwargs: object) -> None:
            raise OSError("no such binary")

        monkeypatch.setattr(codex.subprocess, "Popen", popen)
        result = codex.CodexProvider().run(_usage_spec(tmp_path))
        assert result.exit_code == PROVIDER_FALLBACK_EXIT_CODE
        assert result.tokens is None

    def test_an_expired_deadline_reads_no_reused_log(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        events_log = self._stale(tmp_path)
        seen = _usage_popen(monkeypatch, events=_recorded_codex_turn())
        monkeypatch.setattr(codex.time, "monotonic", lambda: 1000.0)
        result = codex.CodexProvider().run(_usage_spec(tmp_path, deadline=999.0))
        assert result.exit_code == TIMEOUT_EXIT_CODE
        assert "command" not in seen
        assert result.tokens is None
        assert events_log.read_text(encoding="utf-8") == _recorded_codex_turn()
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/unit/headless_agents/test_usage.py tests/unit/headless_agents/test_provider_codex.py -q -k "mark or Usage or usage"`
Expected: FAIL — `ModuleNotFoundError: No module named 'headless_agents.usage'`; once it
exists, `test_the_single_completed_turn_is_measured` fails on `assert None == TokenUsage(...)`.

- [ ] **Step 3: Implement `usage.py`**

```python
"""Usage read from a rail's own stream (spec 2026-10-13 usage and budget §3.1).

A rail reads usage only from the bytes its child wrote in THIS invocation: the offset of
the stream is recorded when the child is launched, and a rail that returns before
launching leaves it unset, so a reused log can never lend an earlier run's usage.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class StreamMark:
    """Where this invocation's bytes start in a stream file; ``None`` until its child launched."""

    offset: int | None = None

    def read(self, path: Path | None) -> str | None:
        """The bytes from the mark on, decoded with replacement; ``None`` when nothing
        launched or the file cannot be read. Never raises."""
        if self.offset is None or path is None:
            return None
        try:
            with path.open("rb") as stream:
                stream.seek(self.offset)
                return stream.read().decode("utf-8", errors="replace")
        except OSError:
            return None


__all__ = ["StreamMark"]
```

- [ ] **Step 4: Implement the codex launch mark** — in `providers/codex.py`:
  add `from ..envelope import codex_usage` and `from ..usage import StreamMark` to the
  imports; add `launch: StreamMark | None = None,` as the last keyword of `run_codex`
  (after `output_schema`), and one docstring paragraph: "``launch`` receives the
  ``events_log`` offset at which this invocation's stream starts, set only once the child
  is launched (spec 2026-10-13 usage §3.1)." In `_run`, the `with` block becomes:

```python
        with (
            events_log.open("w", encoding="utf-8") as events_stream,
            stderr_log.open("w", encoding="utf-8") as stderr_stream,
        ):
            # Spec 2026-10-13 usage §3.1: this invocation's bytes start here.
            start_offset = events_stream.tell()
            popen_kwargs: dict[str, Any] = {}
            if run_environment is not None:
                popen_kwargs["env"] = run_environment
            try:
                process, lifeline = spawn_watched(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=events_stream,
                    stderr=stderr_stream,
                    cwd=runtime_dir,
                    text=True,
                    start_new_session=True,
                    preexec_fn=preexec_for(os.getpid()),
                    **popen_kwargs,
                )
            except OSError as exc:
                stderr_stream.write(f"unable to start Codex: {exc}\n")
                # Codex did not even start: nothing could have been written.
                return PROVIDER_FALLBACK_EXIT_CODE
            if launch is not None:
                launch.offset = start_offset
```

  (the `try: process.communicate(...)` that follows is unchanged). In
  `CodexProvider._run_spec`: create `launch = StreamMark()` before the call, pass
  `launch=launch` to `run_codex`, and replace the `tokens=None` argument and its comment
  with:

```python
                # Spec 2026-10-13 usage §3.1: the single turn.completed this invocation
                # wrote; codex reports no cost (D3).
                tokens=codex_usage(stream) if stream is not None else None,
```

  where `stream = launch.read(spec.events_log)` is computed right after `duration`.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/unit/headless_agents/test_usage.py tests/unit/headless_agents/test_provider_codex.py tests/unit/headless_agents/test_git_tripwire_rails.py -q`
Expected: all pass (the existing `lambda **kwargs:` fakes of `run_codex` accept `launch`).

- [ ] **Step 6: Lint and type-check** — `uv run ruff format src tests && uv run ruff check src tests && uv run mypy src` exit 0.

- [ ] **Step 7: Commit**

```sh
git add src/headless_agents/usage.py
git add src/headless_agents/providers/codex.py
git add tests/unit/headless_agents/test_usage.py
git add tests/unit/headless_agents/test_provider_codex.py
git commit -m "✨ feat(codex): measure the tokens of the turn this invocation wrote"
```

---

### Task 3: agy measurement

**Files:**
- Modify: `src/headless_agents/providers/agy.py` — `run_agy` (587-806: signature, and the
  `with (events_log.open("w") ...)` block of its inner `_run`, 735-755) and
  `AgyProvider.run` (905-982, the `tokens=None` at 970)
- Test: `tests/unit/headless_agents/test_provider_agy.py` (append; reuses `_profile` 57-70,
  `_FakeProcess` 186-216, `_install` 219-246, `_logs` 249-255 and the autouse
  `_host_git_ancestry_stops_at_tmp_path` 258-278)

**Interfaces:**
- Consumes: `agy_usage` (Task 1), `StreamMark` (Task 2).
- Produces: `run_agy(..., launch: StreamMark | None = None) -> int`;
  `AgyProvider.run(spec).tokens` = `agy_usage` of this invocation's bytes; `cost_usd` `None`.

- [ ] **Step 1: Write the failing tests** — append to `test_provider_agy.py` (and add
  `from headless_agents.result import TokenUsage` to its imports; `RunResult` is already
  imported there):

```python
# ── usage (spec 2026-10-13 usage and budget §3.1) ──────────────────────────

USAGE_STREAM = Path(__file__).parent / "fixtures" / "tool_counts" / "agy.events.jsonl"
RECORDED_USAGE = TokenUsage(
    input=768506, output=9307, fresh=133605, cached=634901, thinking=5197
)


def _usage_run(tmp_path: Path, **overrides: object) -> RunResult:
    real_home = tmp_path / "real-home"
    (real_home / ".x").mkdir(parents=True, exist_ok=True)
    (real_home / ".x" / "token").write_text("t", encoding="utf-8")
    (tmp_path / "runtime").mkdir(exist_ok=True)
    fields: dict[str, object] = {
        "prompt": "P",
        "model": "m",
        "profile": _profile(tmp_path),
        "environment": {"PATH": "/usr/bin"},
        **_logs(tmp_path),
    }
    fields.update(overrides)
    provider = agy.AgyProvider(real_home=real_home, ephemeral_root=tmp_path / "runtime")
    return provider.run(RunSpec(**fields))  # type: ignore[arg-type]


def _stale_events(tmp_path: Path) -> Path:
    events_log = _logs(tmp_path)["events_log"]
    events_log.parent.mkdir(parents=True, exist_ok=True)
    events_log.write_text(USAGE_STREAM.read_text(encoding="utf-8"), encoding="utf-8")
    return events_log


class TestAgyUsage:
    def test_the_final_result_event_is_measured(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        stream = USAGE_STREAM.read_text(encoding="utf-8")
        _install(monkeypatch, _FakeProcess(returncode=0, events=stream))
        result = _usage_run(tmp_path)
        assert result.exit_code == 0
        assert result.tokens == RECORDED_USAGE
        assert result.cost_usd is None

    def test_a_refusal_reads_no_reused_log(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        events_log = _stale_events(tmp_path)
        captured = _install(monkeypatch, _FakeProcess(returncode=0))
        result = _usage_run(tmp_path, profile=_profile(tmp_path, guard=None))
        assert result.exit_code == 1, "no guard: refused as today"
        assert "command" not in captured
        assert result.tokens is None
        assert events_log.read_text(encoding="utf-8") == USAGE_STREAM.read_text(encoding="utf-8")

    def test_a_launch_failure_reads_no_reused_log(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _stale_events(tmp_path)

        def popen(command: list[str], **kwargs: object) -> None:
            raise OSError("no such binary")

        monkeypatch.setattr(agy, "guard_denies_machine_tools", lambda path: True)
        monkeypatch.setattr(agy.subprocess, "Popen", popen)
        result = _usage_run(tmp_path)
        assert result.exit_code == PROVIDER_FALLBACK_EXIT_CODE
        assert result.tokens is None

    def test_an_expired_deadline_reads_no_reused_log(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        events_log = _stale_events(tmp_path)
        captured = _install(monkeypatch, _FakeProcess(returncode=0))
        monkeypatch.setattr(agy.time, "monotonic", lambda: 1000.0)
        result = _usage_run(tmp_path, deadline=999.0)
        assert result.exit_code == TIMEOUT_EXIT_CODE
        assert "command" not in captured
        assert result.tokens is None
        assert events_log.read_text(encoding="utf-8") == USAGE_STREAM.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/unit/headless_agents/test_provider_agy.py -q -k AgyUsage`
Expected: FAIL — `test_the_final_result_event_is_measured`: `assert None == TokenUsage(input=133605, ...)`.

- [ ] **Step 3: Implement** — in `providers/agy.py`: add `from ..envelope import agy_usage`
  and `from ..usage import StreamMark`; add `launch: StreamMark | None = None,` as the last
  keyword of `run_agy` (after `guard_proven`) with the same docstring sentence as codex.
  In `_run`, inside `with (events_log.open("w", ...) as events_stream, stderr_log.open(...)
  as stderr_stream):`, first line `start_offset = events_stream.tell()`; right after the
  `except OSError` branch of `spawn_watched` (which still returns
  `PROVIDER_FALLBACK_EXIT_CODE`):

```python
            if launch is not None:
                launch.offset = start_offset
```

  In `AgyProvider.run`: `launch = StreamMark()` before `run_agy(...)`, pass `launch=launch`,
  then after `duration`: `stream = launch.read(spec.events_log)`, and the result's
  `tokens=None` becomes:

```python
                # Spec 2026-10-13 usage §3.1: the final result event this invocation
                # wrote; agy reports no cost (D3).
                tokens=agy_usage(stream) if stream is not None else None,
```

  The refusal `RunResult` (`argv_prompt_or_refusal`, before launch) keeps `tokens=None`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/headless_agents/test_provider_agy.py tests/unit/headless_agents/test_git_tripwire_rails.py -q`
Expected: all pass.

- [ ] **Step 5: Lint and type-check** — `uv run ruff format src tests && uv run ruff check src tests && uv run mypy src` exit 0.

- [ ] **Step 6: Commit**

```sh
git add src/headless_agents/providers/agy.py
git add tests/unit/headless_agents/test_provider_agy.py
git commit -m "✨ feat(agy): measure the tokens of the result this invocation wrote"
```

---

### Task 4: claude prints its JSON envelope on every run

**Files:**
- Modify: `src/headless_agents/providers/claude.py` — imports (17-49), constants (after
  `RESULT_ENVELOPE_NAME`, 61), `build_claude_command` (111-177), `run_claude` (334-508),
  `ClaudeProvider.run` (690-784)
- Modify: `src/headless_agents/spec.py:5-9` (module docstring sentence on `run_claude`)
- Test: `tests/unit/headless_agents/test_provider_claude.py` — update
  `TestBuildClaudeCommand.test_with_a_server_allows_exactly_its_tools` (109-131),
  `_FakeProcess` (388-415), `_install` (418-429), `TestRunClaude` (446-560),
  `TestClaudeProvider` (575-700), `TestStructuredOutput` argv tests (1085-1135); append
  `TestJsonOnEveryRun`

**Interfaces:**
- Consumes: `extract_claude` (Task 1).
- Produces:
  - `ENVELOPE_SUFFIX: Final = ".envelope.json"`, `ANSWER_SUFFIX: Final = ".answer.txt"`
  - `claude_envelope_path(raw_log: Path, answer_log: Path | None) -> Path` (raises `ValueError` when it equals one of the logs)
  - `claude_answer_path(raw_log: Path, answer_log: Path | None) -> Path`
  - `envelope_answer(stdout: str) -> str`
  - `run_claude(...) -> int`: unchanged signature; stdout now to the envelope file when no schema
  - `ClaudeProvider.run`: `report_path` = `<raw_log>.answer.txt` for a raw-log-only spec

- [ ] **Step 1: Update the existing tests that pin the old streams** (they must fail
  against today's code, then pass):

In `TestBuildClaudeCommand.test_with_a_server_allows_exactly_its_tools`, the expected list
becomes:

```python
        assert command == [
            "claude",
            "-p",
            "-",
            "--model",
            "m",
            "--max-turns",
            "3",
            "--output-format",
            "json",
            "--permission-mode",
            "bypassPermissions",
            "--tools",
            "",
            "--allowedTools",
            "mcp__example__example_search,mcp__example__example_get",
            "--mcp-config",
            str(tmp_path / "mcp.json"),
            "--strict-mcp-config",
        ]
```

Replace `_FakeProcess` and `_install` with:

```python
class _FakeProcess:
    def __init__(
        self, *, returncode: int, output: str = "", hang: bool = False, err: str = ""
    ) -> None:
        self._returncode = returncode
        self._output = output
        self._err = err
        self._hang = hang
        self.returncode: int | None = None
        self.pid = 4242

    def bind(self, stream: object, err_stream: object = None) -> None:
        self._stream = stream
        self._err_stream = err_stream

    def communicate(
        self, input: str | None = None, timeout: float | None = None
    ) -> tuple[None, None]:
        if self._hang:
            raise subprocess.TimeoutExpired(cmd="claude", timeout=timeout or 0)
        self._stream.write(self._output)  # type: ignore[attr-defined]
        # A real child writes through the file descriptor; the fake writes through a
        # buffered Python stream, so it flushes, or a read of the file sees nothing.
        self._stream.flush()  # type: ignore[attr-defined]
        if self._err:
            self._err_stream.write(self._err)  # type: ignore[union-attr]
            self._err_stream.flush()  # type: ignore[union-attr]
        self.returncode = self._returncode
        return None, None

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode or 0

    def kill(self) -> None:
        self.returncode = -9


def _install(monkeypatch: pytest.MonkeyPatch, fake: _FakeProcess) -> dict[str, object]:
    captured: dict[str, object] = {}

    def popen(command: list[str], **kwargs: object) -> _FakeProcess:
        captured["command"] = command
        captured["kwargs"] = kwargs
        fake.bind(kwargs["stdout"], kwargs["stderr"])
        return fake

    monkeypatch.setattr(claude.subprocess, "Popen", popen)
    monkeypatch.setattr(claude, "terminate_process_group", lambda process: process.kill())
    return captured
```

In `TestRunClaude.test_writes_the_mcp_config_into_a_private_runtime_dir_and_runs_there`,
replace the last two asserts with:

```python
        out = tmp_path / "out"
        assert kwargs["stderr"] is not subprocess.STDOUT
        assert (out / "raw.log").read_text(encoding="utf-8") == ""
        assert (out / "raw.log.envelope.json").read_text(encoding="utf-8") == "ok\n"
        assert (out / "raw.log.answer.txt").read_text(encoding="utf-8") == "ok\n"
```

In `test_a_failure_after_a_completed_call_keeps_its_own_code` and
`test_a_childs_own_fallback_code_after_a_completed_call_never_advances_a_chain`, the OTEL
record travels on stderr, as it does from the real CLI: `_FakeProcess(returncode=5,
output=output)` becomes `_FakeProcess(returncode=5, err=output)`, and
`_FakeProcess(returncode=code, output=output)` becomes `_FakeProcess(returncode=code,
err=output)`.

In the test that defines `class TwoStreams(_FakeProcess)` (`test_provider_claude.py:539-546`),
flush both streams after writing, for the same reason (the envelope is read from its file
while the stream is still open): after `self.streams["stdout"].write("the answer\n")` add
`self.streams["stdout"].flush()`, and after the stderr write add
`self.streams["stderr"].flush()`.

In `TestClaudeProvider.test_run_forwards_and_reports`, the report path assert becomes
`assert result.report_path == tmp_path / "raw.log.answer.txt"`.

Replace `TestClaudeProvider.test_text_is_only_what_this_run_appended_to_a_reused_log` and
`test_an_answer_that_is_json_comes_back_verbatim` with:

```python
    def test_a_raw_log_only_run_reads_its_own_answer_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        raw_log = tmp_path / "raw.log"
        raw_log.write_text("PREVIOUS RUN\n", encoding="utf-8")
        answer = tmp_path / "raw.log.answer.txt"
        answer.write_text("PREVIOUS ANSWER\n", encoding="utf-8")

        def fake_run_claude(**kwargs: object) -> int:
            assert kwargs["answer_log"] is None
            assert not answer.exists(), "a stale answer must be gone before the run"
            answer.write_text("THIS RUN\n", encoding="utf-8")
            return 0

        monkeypatch.setattr(claude, "run_claude", fake_run_claude)
        result = claude.ClaudeProvider().run(RunSpec(prompt="P", model="m", raw_log=raw_log))
        assert result.text == "THIS RUN\n"
        assert result.report_path == answer

    def test_an_answer_that_is_json_comes_back_verbatim(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A judge's verdict is JSON: the answer file holds it as written, never re-read
        as an envelope."""
        verdict = '{"result": "pass", "score": 3}\n'

        def fake_run_claude(**kwargs: object) -> int:
            raw = kwargs["raw_log"]
            assert isinstance(raw, Path)
            claude.claude_answer_path(raw, None).write_text(verdict, encoding="utf-8")
            return 0

        monkeypatch.setattr(claude, "run_claude", fake_run_claude)
        result = claude.ClaudeProvider().run(
            RunSpec(prompt="P", model="m", raw_log=tmp_path / "raw.log")
        )
        assert result.text == verdict
```

In `TestStructuredOutput`, replace `test_argv_without_a_schema_is_unchanged` and
`test_argv_with_a_schema_adds_the_json_output_and_the_inline_schema` with:

```python
    def test_argv_without_a_schema_asks_for_the_json_envelope(self, tmp_path: Path) -> None:
        config = tmp_path / "m.json"
        assert claude.build_claude_command(
            model="m", max_turns=3, mcp_config_path=config, mcp=None
        ) == [
            "claude",
            "-p",
            "-",
            "--model",
            "m",
            "--max-turns",
            "3",
            "--output-format",
            "json",
            "--permission-mode",
            "bypassPermissions",
            "--tools",
            "",
            "--mcp-config",
            str(config),
            "--strict-mcp-config",
        ]
        for workspace in (None, Workspace(path=tmp_path), Workspace(path=tmp_path, write=True)):
            base = claude.build_claude_command(
                model="m", max_turns=3, mcp_config_path=config, mcp=None, workspace=workspace
            )
            none = claude.build_claude_command(
                model="m",
                max_turns=3,
                mcp_config_path=config,
                mcp=None,
                workspace=workspace,
                json_schema=None,
            )
            assert none == base

    def test_argv_with_a_schema_adds_only_the_inline_schema(self, tmp_path: Path) -> None:
        config = tmp_path / "m.json"
        for workspace in (None, Workspace(path=tmp_path), Workspace(path=tmp_path, write=True)):
            base = claude.build_claude_command(
                model="m", max_turns=3, mcp_config_path=config, mcp=None, workspace=workspace
            )
            with_schema = claude.build_claude_command(
                model="m",
                max_turns=3,
                mcp_config_path=config,
                mcp=None,
                workspace=workspace,
                json_schema=SCHEMA_TEXT,
            )
            i = base.index("--max-turns") + 2
            assert base[i : i + 2] == ["--output-format", "json"]
            assert with_schema == base[: i + 2] + ["--json-schema", SCHEMA_TEXT] + base[i + 2 :]
```

- [ ] **Step 2: Append the new behaviour tests** at the end of `test_provider_claude.py`:

```python
# ── JSON on every run (spec 2026-10-13 usage and budget §3.1, D2) ───────────


class TestJsonOnEveryRun:
    def test_the_answer_log_gets_the_result_and_the_envelope_stays_beside_it(
        self, tmp_path: Path
    ) -> None:
        stdout = tmp_path / "stdout.json"
        stdout.write_text(
            json.dumps({"type": "result", "result": "the answer\n", "modelUsage": {}}),
            encoding="utf-8",
        )
        answer = tmp_path / "out" / "report.log"
        code = claude.run_claude(
            prompt="p",
            model="m",
            max_turns=1,
            timeout_seconds=30,
            raw_log=tmp_path / "out" / "raw.log",
            mcp=None,
            answer_log=answer,
            executable=_envelope_fake(tmp_path, stdout),
            environment={"PATH": "/usr/bin:/bin"},
        )
        assert code == 0
        assert answer.read_text(encoding="utf-8") == "the answer\n"
        envelope = tmp_path / "out" / "report.log.envelope.json"
        assert envelope.read_bytes() == stdout.read_bytes()

    def test_a_raw_log_only_caller_gets_the_answer_file_and_stderr_alone_in_raw_log(
        self, tmp_path: Path
    ) -> None:
        stdout = tmp_path / "stdout.json"
        stdout.write_text(json.dumps({"type": "result", "result": "ANSWER"}), encoding="utf-8")
        executable = _fake(tmp_path, f'cat "{stdout}"\necho "a warning" >&2')
        raw_log = tmp_path / "raw.log"
        result = claude.ClaudeProvider().run(
            RunSpec(
                prompt="P",
                model="m",
                raw_log=raw_log,
                executable=executable,
                environment={"PATH": "/usr/bin:/bin"},
            )
        )
        assert result.exit_code == 0
        assert result.text == "ANSWER"
        assert result.report_path == tmp_path / "raw.log.answer.txt"
        assert (tmp_path / "raw.log.answer.txt").read_text(encoding="utf-8") == "ANSWER"
        assert raw_log.read_text(encoding="utf-8") == "a warning\n"

    def test_stdout_that_is_not_json_is_the_answer_verbatim(self, tmp_path: Path) -> None:
        executable = _fake(tmp_path, "printf 'plain text\\n'")
        result = claude.ClaudeProvider().run(
            RunSpec(
                prompt="P",
                model="m",
                raw_log=tmp_path / "raw.log",
                report_log=tmp_path / "report.log",
                executable=executable,
                environment={"PATH": "/usr/bin:/bin"},
            )
        )
        assert result.exit_code == 0
        assert result.text == "plain text\n"

    def test_two_concurrent_runs_in_one_directory_keep_distinct_envelopes(
        self, tmp_path: Path
    ) -> None:
        from concurrent.futures import ThreadPoolExecutor

        logs = tmp_path / "logs"

        def run(name: str) -> int:
            body = tmp_path / f"{name}.json"
            body.write_text(json.dumps({"type": "result", "result": name}), encoding="utf-8")
            script = tmp_path / f"fake-{name}"
            script.write_text(
                f'#!/usr/bin/env bash\ncat >/dev/null\nsleep 0.2\ncat "{body}"\n',
                encoding="utf-8",
            )
            script.chmod(0o755)
            return claude.run_claude(
                prompt="p",
                model="m",
                max_turns=1,
                timeout_seconds=30,
                raw_log=logs / f"{name}.raw.log",
                mcp=None,
                answer_log=logs / f"{name}.report.log",
                executable=str(script),
                environment={"PATH": "/usr/bin:/bin"},
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(pool.map(run, ["first", "second"]))
        assert codes == [0, 0]
        for name in ("first", "second"):
            envelope = json.loads(
                (logs / f"{name}.report.log.envelope.json").read_text(encoding="utf-8")
            )
            assert envelope["result"] == name
            assert (logs / f"{name}.report.log").read_text(encoding="utf-8") == name

    def test_an_envelope_path_that_is_a_log_is_refused_before_anything_runs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        def trap(*args: object, **kwargs: object) -> None:
            pytest.fail("claude was started with its envelope on one of its logs")

        monkeypatch.setattr(claude, "spawn_watched", trap)
        raw_log = tmp_path / "report.log.envelope.json"
        with pytest.raises(ValueError, match="envelope"):
            claude.run_claude(
                prompt="p",
                model="m",
                max_turns=1,
                timeout_seconds=30,
                raw_log=raw_log,
                mcp=None,
                answer_log=tmp_path / "report.log",
            )
        with pytest.raises(ValueError, match="envelope"):
            claude.ClaudeProvider().run(
                RunSpec(prompt="P", model="m", raw_log=raw_log, report_log=tmp_path / "report.log")
            )
        assert not raw_log.exists()
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest tests/unit/headless_agents/test_provider_claude.py -q`
Expected: FAIL — among others `test_with_a_server_allows_exactly_its_tools` (no
`--output-format`), `test_writes_the_mcp_config...` (`stderr is subprocess.STDOUT`),
`TestJsonOnEveryRun::*` (`AttributeError: ... has no attribute 'claude_answer_path'` or
the answer read from `raw_log`).

- [ ] **Step 4: Implement** in `providers/claude.py`.

Imports: `from typing import IO, Final` (replacing `from typing import Final`) and
`from ..envelope import extract_claude`. After `RESULT_ENVELOPE_NAME`:

```python
#: Beside the log the caller chose for the answer (``answer_log``, else ``raw_log``): the
#: JSON envelope a run WITHOUT a schema prints on stdout (spec 2026-10-13 usage §3.1).
ENVELOPE_SUFFIX: Final = ".envelope.json"
#: Beside ``raw_log`` when the caller passed no ``answer_log``: the answer read from it.
ANSWER_SUFFIX: Final = ".answer.txt"


def _beside(path: Path, suffix: str) -> Path:
    return path.with_name(path.name + suffix)


def claude_envelope_path(raw_log: Path, answer_log: Path | None) -> Path:
    """Where a run without a schema keeps claude's stdout envelope.

    Named after the caller's own log, so two invocations with distinct logs never share
    one. ``ValueError`` when that path IS one of the caller's logs: the envelope is
    truncated when the child starts and would destroy it.
    """
    envelope = _beside(answer_log if answer_log is not None else raw_log, ENVELOPE_SUFFIX)
    logs = {raw_log.resolve()} | ({answer_log.resolve()} if answer_log is not None else set())
    if envelope.resolve() in logs:
        raise ValueError(
            f"claude keeps its JSON envelope in {envelope}, which is already a log of this "
            "run: name the logs differently"
        )
    return envelope


def claude_answer_path(raw_log: Path, answer_log: Path | None) -> Path:
    """Where the answer of a run without a schema lands: ``answer_log``, else
    ``<raw_log>.answer.txt`` -- ``raw_log`` holds stderr only."""
    return answer_log if answer_log is not None else _beside(raw_log, ANSWER_SUFFIX)


def envelope_answer(stdout: str) -> str:
    """The answer a run without a schema printed: the envelope's ``result``.

    Stdout that is not a JSON object is the answer verbatim; an envelope without a
    ``result`` string (a max-turns failure) answers nothing.
    """
    envelope = extract_claude(stdout)
    if envelope is None:
        return stdout
    return envelope.text or ""


def _read_envelope_text(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return ""
```

`build_claude_command`: the command start becomes

```python
    command = [
        executable, "-p", "-", "--model", model, "--max-turns", str(max_turns),
        "--output-format", "json",
    ]  # fmt: skip
    if json_schema is not None:
        command.append("--json-schema")
        command.append(json_schema)
```

and its docstring paragraph on `json_schema` becomes: "Every run asks for
``--output-format json`` right after ``--max-turns`` (spec 2026-10-13 usage §3.1, D2): the
envelope carries the answer, the tokens and the cost. ``json_schema`` -- compact JSON,
inline, as claude takes it -- adds ``--json-schema`` right after it (0.5.3 lot 1)."

`run_claude`: the docstring's first two paragraphs become: "Without a schema, stdout is
claude's JSON envelope and goes to :func:`claude_envelope_path` (truncated when the child
starts); once the child has exited, the envelope's answer (:func:`envelope_answer`) is
written to :func:`claude_answer_path` -- ``answer_log`` when given, else
``<raw_log>.answer.txt``. stderr -- the OTEL console stream, any CLI warning -- always lands
in ``raw_log``, where :func:`tool_call_completed` reads it. With ``json_schema`` nothing
moves: stdout goes to ``answer_log`` (or mixes into ``raw_log`` without one), as before."
Code, right after the `timeout_seconds` check:

```python
    # Spec 2026-10-13 usage §3.1: refused before any file exists.
    envelope = None if json_schema is not None else claude_envelope_path(raw_log, answer_log)
```

after `answer_log = answer_log.resolve()` / mkdir:

```python
    if envelope is None:
        answer_target = answer_log
    else:
        envelope = envelope.resolve()
        answer_target = claude_answer_path(raw_log, answer_log)
```

and the block from `answer_context = (` down to the end of the `try/except/finally`
becomes:

```python
        answer_context = (
            answer_target.open("w", encoding="utf-8")
            if answer_target is not None
            else nullcontext()
        )
        envelope_context = (
            envelope.open("w", encoding="utf-8") if envelope is not None else nullcontext()
        )
        with (
            raw_log.open("a", encoding="utf-8") as raw_stream,
            answer_context as answer_stream,
            envelope_context as envelope_stream,
        ):
            stderr: IO[str] | int = raw_stream
            if envelope_stream is not None:
                stdout = envelope_stream
            elif answer_stream is not None:
                stdout = answer_stream
            else:
                stdout, stderr = raw_stream, subprocess.STDOUT
            try:
                process, lifeline = spawn_watched(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=stdout,
                    stderr=stderr,
                    cwd=cwd,
                    env=run_environment,
                    text=True,
                    start_new_session=True,
                    preexec_fn=preexec_for(os.getpid()),
                )
            except OSError as exc:
                raw_stream.write(f"unable to start Claude: {exc}\n")
                # Claude did not start: nothing could have been written.
                return PROVIDER_FALLBACK_EXIT_CODE
            # ... the existing try: process.communicate / except TimeoutExpired /
            # except BaseException / finally block, unchanged ...
            if envelope is not None and answer_stream is not None:
                # Spec 2026-10-13 usage §3.1: the answer is the envelope's result.
                answer_stream.write(envelope_answer(_read_envelope_text(envelope)))
```

(keep the existing `try/except/finally` verbatim where the comment says so; the new `if`
follows it inside the `with`). Everything after the `with` is unchanged.

`ClaudeProvider.run`: replace the comment and `answer_log = spec.report_log` /
`json_schema, envelope_path = self._structured(spec)` lines with:

```python
        # Spec 2026-10-13 usage §3.1: claude prints its JSON envelope on every run. A
        # schema run keeps claude-result.json; any other keeps it beside the caller's
        # log, refused here, before any file is touched, when it would be one of them.
        answer_log = spec.report_log
        json_schema, envelope_path = self._structured(spec)
        if envelope_path is None:
            envelope_path = claude_envelope_path(raw_log, answer_log)
        report_path = claude_answer_path(raw_log, answer_log)
```

In the preamble-refusal `RunResult` and the final `RunResult`, `report_path=answer_log if
answer_log is not None else raw_log` becomes `report_path=report_path`. Replace the
`offset = ...` line and the `if envelope_path is not None: envelope_path.unlink(...)` block
with:

```python
        # Only this run's envelope and answer may be read: a run that never starts must
        # not find what an earlier run left beside a reused log.
        envelope_path.unlink(missing_ok=True)
        if answer_log is None:
            report_path.unlink(missing_ok=True)
```

pass `answer_log=envelope_path if json_schema is not None else answer_log` to `run_claude`
(a schema run still hands claude-result.json as its stdout file), and replace the text
reading (`if envelope_path is not None: ... else: text = answer_text(raw_log, ...,
offset=offset)`) with:

```python
        if json_schema is not None:
            assert answer_log is not None, "_structured requires a report_log"
            exit_code, text = _settle_structured(exit_code, envelope_path, answer_log, raw_log)
        else:
            text = answer_text(report_path, exit_code=exit_code)
```

`spec.py` module docstring: the sentence "``run_claude`` mixes stdout/stderr into ONE file,
and a fallback between the two would invent a mapping the caller did not make" becomes
"``raw_log`` receives claude's stderr (its OTEL console stream), while its answer goes to
``report_log`` or beside ``raw_log``; a fallback between the logs would invent a mapping the
caller did not make".

- [ ] **Step 5: Run the claude tests and every rail test**

Run: `uv run pytest tests/unit/headless_agents/test_provider_claude.py tests/unit/headless_agents/test_git_tripwire_rails.py tests/unit/headless_agents/test_engine_execute.py -q`
Expected: all pass (`TestStructuredOutput` envelope tests unchanged and green; the
schema argv is byte-identical to before).

- [ ] **Step 6: Lint and type-check** — `uv run ruff format src tests && uv run ruff check src tests && uv run mypy src` exit 0.

- [ ] **Step 7: Commit**

```sh
git add src/headless_agents/providers/claude.py
git add src/headless_agents/spec.py
git add tests/unit/headless_agents/test_provider_claude.py
git commit -m "✨ feat(claude): print the JSON envelope on every run and read the answer from it"
```

---

### Task 5: claude records its tokens and cost

**Files:**
- Modify: `src/headless_agents/providers/claude.py` — `ClaudeProvider.run` final `RunResult`
  (the `tokens=None` of 775) and a new `_measure`
- Test: `tests/unit/headless_agents/test_provider_claude.py` — update
  `TestStructuredOutput.test_a_measured_success_envelope_gives_the_structured_output_as_text`
  (its last line), append `TestClaudeMeasures`

**Interfaces:**
- Consumes: `extract_claude`, `claude_usage` (Task 1); `claude_envelope_path`,
  `_read_envelope_text` (Task 4).
- Produces: `ClaudeProvider.run(spec).tokens` / `.cost_usd` from this invocation's envelope
  (schema runs: `claude-result.json`; others: `<log>.envelope.json`).

- [ ] **Step 1: Write the failing tests.** In
  `test_a_measured_success_envelope_gives_the_structured_output_as_text`, the last line
  `assert result.tokens is None and result.cost_usd is None` becomes
  `assert (result.tokens, result.cost_usd) == (TokenUsage(input=9499, output=100, fresh=9499, cached=0, thinking=43), 0.019488)`.
  Add `from headless_agents.result import TokenUsage` to the imports, and append:

```python
# ── tokens and cost (spec 2026-10-13 usage and budget §3.1) ────────────────

USAGE_FIXTURES = Path(__file__).parent / "fixtures" / "usage"
MAX_TURNS_USAGE = TokenUsage(input=30004, output=531, fresh=10368, cached=19636, thinking=350)


class TestClaudeMeasures:
    def test_a_run_without_a_schema_measures_and_answers(self, tmp_path: Path) -> None:
        fixture = USAGE_FIXTURES / "claude.no-schema.json"
        document = json.loads(fixture.read_text(encoding="utf-8"))
        run_dir = tmp_path / "runs" / "r1"
        result = claude.ClaudeProvider().run(
            _schema_spec(tmp_path, _envelope_fake(tmp_path, fixture), output_schema=None)
        )
        assert result.exit_code == 0
        assert result.text == document["result"]
        assert (run_dir / "report.log").read_text(encoding="utf-8") == document["result"]
        assert (run_dir / "report.log.envelope.json").read_bytes() == fixture.read_bytes()
        assert result.cost_usd == document["total_cost_usd"]
        assert result.tokens is not None
        assert result.tokens.input is not None and result.tokens.input > 0
        written = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
        assert written == result.to_dict()

    def test_two_models_are_summed(self, tmp_path: Path) -> None:
        fixture = USAGE_FIXTURES / "claude.two-models.synthetic.json"
        result = claude.ClaudeProvider().run(
            _schema_spec(tmp_path, _envelope_fake(tmp_path, fixture), output_schema=None)
        )
        assert result.exit_code == 0
        assert result.text == '{"ok":true}'
        assert result.tokens == TokenUsage(
            input=10504, output=120, fresh=9504, cached=1000, thinking=43
        )
        assert result.cost_usd == 0.020488

    def test_a_max_turns_envelope_measures_but_answers_nothing(self, tmp_path: Path) -> None:
        """No ``result`` string: the measure stands, the answer and the code stay as
        today -- exit 1 with no tool call read as provider unavailable (3)."""
        fixture = FIXTURES / "claude-2.1.283.max-turns.json"
        run_dir = tmp_path / "runs" / "r1"
        result = claude.ClaudeProvider().run(
            _schema_spec(tmp_path, _envelope_fake(tmp_path, fixture, code=1), output_schema=None)
        )
        assert result.exit_code == PROVIDER_FALLBACK_EXIT_CODE
        assert result.text is None
        assert (run_dir / "report.log").read_text(encoding="utf-8") == ""
        assert result.tokens == MAX_TURNS_USAGE
        assert result.cost_usd == 0.025328600000000003

    def test_a_schema_run_that_ran_out_of_turns_still_measures(self, tmp_path: Path) -> None:
        fixture = FIXTURES / "claude-2.1.283.max-turns.json"
        result = claude.ClaudeProvider().run(
            _schema_spec(tmp_path, _envelope_fake(tmp_path, fixture, code=1))
        )
        assert result.exit_code == 1
        assert result.text is None
        assert (result.tokens, result.cost_usd) == (MAX_TURNS_USAGE, 0.025328600000000003)

    def test_stdout_that_is_not_json_measures_nothing(self, tmp_path: Path) -> None:
        result = claude.ClaudeProvider().run(
            _schema_spec(tmp_path, _fake(tmp_path, "printf 'plain\\n'"), output_schema=None)
        )
        assert result.exit_code == 0
        assert result.text == "plain\n"
        assert (result.tokens, result.cost_usd) == (None, None)

    def test_a_stale_envelope_lends_nothing_to_a_run_that_never_started(
        self, tmp_path: Path
    ) -> None:
        run_dir = tmp_path / "runs" / "r1"
        run_dir.mkdir(parents=True)
        success = FIXTURES / "claude-2.1.283.success.json"
        (run_dir / "report.log.envelope.json").write_bytes(success.read_bytes())
        spec = _schema_spec(
            tmp_path,
            _envelope_fake(tmp_path, success),
            output_schema=None,
            environment={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "no-login")},
        )
        result = claude.ClaudeProvider().run(spec)
        assert result.exit_code == PROVIDER_FALLBACK_EXIT_CODE
        assert (result.tokens, result.cost_usd) == (None, None)
        assert not (run_dir / "report.log.envelope.json").exists()

    def test_nothing_is_read_when_the_child_never_started(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        def no_read(path: Path) -> str:
            raise AssertionError(f"read {path} although nothing launched")

        monkeypatch.setattr(claude, "_read_envelope_text", no_read)
        success = FIXTURES / "claude-2.1.283.success.json"
        no_login = _schema_spec(
            tmp_path,
            _envelope_fake(tmp_path, success),
            output_schema=None,
            environment={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "no-login")},
        )
        refused = claude.ClaudeProvider().run(no_login)
        assert refused.exit_code == PROVIDER_FALLBACK_EXIT_CODE
        assert (refused.tokens, refused.cost_usd) == (None, None)
        absent = _schema_spec(
            tmp_path, str(tmp_path / "no-such-claude"), output_schema=None
        )
        failed = claude.ClaudeProvider().run(absent)
        assert failed.exit_code == PROVIDER_FALLBACK_EXIT_CODE
        assert (failed.tokens, failed.cost_usd) == (None, None)

    def test_a_missing_bearer_refuses_before_the_launch_mark(self, tmp_path: Path) -> None:
        mark = StreamMark()
        code = claude.run_claude(
            prompt="p",
            model="m",
            max_turns=1,
            timeout_seconds=30,
            raw_log=tmp_path / "raw.log",
            mcp=McpServer(name="brain", url=URL, tools=("brain_search",)),
            environment={"PATH": "/usr/bin:/bin"},
            launch=mark,
        )
        assert code == 1
        assert mark.offset is None

    def test_a_launched_child_sets_the_mark(self, tmp_path: Path) -> None:
        mark = StreamMark()
        stdout = tmp_path / "stdout.json"
        stdout.write_text(json.dumps({"result": "x"}), encoding="utf-8")
        code = claude.run_claude(
            prompt="p",
            model="m",
            max_turns=1,
            timeout_seconds=30,
            raw_log=tmp_path / "raw.log",
            mcp=None,
            executable=_envelope_fake(tmp_path, stdout),
            environment={"PATH": "/usr/bin:/bin"},
            launch=mark,
        )
        assert code == 0
        assert mark.offset == 0

    def test_an_empty_object_answers_nothing_and_measures_nothing(self, tmp_path: Path) -> None:
        result = claude.ClaudeProvider().run(
            _schema_spec(tmp_path, _fake(tmp_path, "printf '{}'"), output_schema=None)
        )
        assert result.exit_code == 0
        assert result.text is None
        assert (tmp_path / "runs" / "r1" / "report.log").read_text(encoding="utf-8") == ""
        assert (result.tokens, result.cost_usd) == (None, None)
```

These tests also need `from headless_agents.usage import StreamMark` in the test file's
imports (`McpServer` and `URL` are already there).

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/unit/headless_agents/test_provider_claude.py -q -k "Measures or measured_success"`
Expected: FAIL — `assert None == TokenUsage(...)` (claude still records `tokens=None`);
`test_stdout_that_is_not_json_measures_nothing` and the stale test already pass.

- [ ] **Step 3: Implement** — imports: `from ..envelope import claude_usage, extract_claude`
  and `from ..result import RunResult, TokenUsage`. Next to `_read_envelope_text`:

```python
def _measure(
    envelope_path: Path, launch: StreamMark
) -> tuple[TokenUsage | None, float | None]:
    """``(tokens, cost_usd)`` of this invocation's envelope; ``(None, None)`` without one.

    Nothing is read unless the child launched (spec 2026-10-13 usage §3.1): a refusal
    or a launch failure leaves ``launch.offset`` at ``None``. Independent of the answer:
    an envelope without a ``result`` (max turns) still measures; stdout that is not a
    JSON object does not.
    """
    if launch.offset is None:
        return None, None
    envelope = extract_claude(_read_envelope_text(envelope_path))
    if envelope is None:
        return None, None
    return claude_usage(envelope), envelope.cost_usd
```

  Also `from ..usage import StreamMark`. `run_claude` gains a last keyword
  `launch: StreamMark | None = None` (docstring: "set to offset 0 once the child launched:
  the envelope is truncated at launch, so the whole file is this invocation's"); right
  after the successful `spawn_watched(...)` call (after its `except OSError` block, before
  `process.communicate`), add `if launch is not None: launch.offset = 0`. In
  `ClaudeProvider.run`, create `launch = StreamMark()` before calling `run_claude` and pass
  `launch=launch`; after the text is settled: `tokens, cost = _measure(envelope_path, launch)`;
  the final `RunResult` takes `tokens=tokens` and `cost_usd=cost` (the preamble-refusal
  `RunResult` keeps `tokens=None`).

- [ ] **Step 4: Run the tests** — `uv run pytest tests/unit/headless_agents/test_provider_claude.py tests/unit/headless_agents/test_git_tripwire_rails.py -q` → all pass.

- [ ] **Step 5: Lint and type-check** — `uv run ruff format src tests && uv run ruff check src tests && uv run mypy src` exit 0.

- [ ] **Step 6: Commit**

```sh
git add src/headless_agents/providers/claude.py
git add tests/unit/headless_agents/test_provider_claude.py
git commit -m "✨ feat(claude): record the tokens and cost of every run's envelope"
```

---

### Task 6: `run.json` — attempts, run totals and the summary line

**Files:**
- Modify: `src/headless_agents/report.py` — `RUN_KEYS`/`STEP_KEYS` (27-37), `new_report`
  (40-61), `step_entry` (69-99), `refused_step_entry` (102-107), `with_step` (110-120),
  `__all__` (127-140)
- Test: `tests/unit/headless_agents/test_report.py` (update `test_the_run_key_set_is_the_spec_one`
  47-54, append the rest)

**Interfaces:**
- Consumes: `RunResult`, `TokenUsage`.
- Produces:
  - `RUN_KEYS` = today's keys with `"tokens", "tokens_complete"` between `"cost_complete"` and `"steps"`; `STEP_KEYS` = today's keys + `"attempts"` last
  - `TOKEN_FIELDS: Final = ("input", "output", "fresh", "cached", "thinking")`
  - `attempt_entry(result: RunResult) -> dict[str, object]`
  - `step_entry(..., attempts: Sequence[RunResult] = ()) -> dict[str, object]`
  - `run_tokens(spenders: Sequence[Mapping[str, object]]) -> dict[str, int | None] | None`
  - `summary_line(report: Mapping[str, object]) -> str`

- [ ] **Step 1: Write the failing tests.** In `test_the_run_key_set_is_the_spec_one` the
  pinned tuple becomes:

```python
    assert RUN_KEYS == (
        "schema", "kind", "run_id", "target", "status", "exit_code", "verdict", "text",
        "repository", "base", "head", "branch", "lineage", "continues", "findings_from",
        "implement_providers", "commits", "failure_reason", "vendor_check", "cleanup", "pid",
        "started_at", "duration_seconds", "cost_usd", "cost_complete", "tokens",
        "tokens_complete", "steps",
    )  # fmt: skip
```

  Change the import block to also import `refused_step_entry` and `summary_line`, and append:

```python
# ── usage (spec 2026-10-13 usage and budget §3.2, §3.3) ─────────────────────

TEN_TWO = {"input": 10, "output": 2, "fresh": None, "cached": None, "thinking": None}


def test_the_step_key_set_ends_with_attempts() -> None:
    assert STEP_KEYS[-1] == "attempts"
    assert "budget" not in STEP_KEYS and "partial_answer" not in STEP_KEYS


def test_a_new_report_has_empty_complete_totals() -> None:
    report = _report()
    assert (report["tokens"], report["tokens_complete"]) == (None, True)


def test_an_earlier_link_lands_in_attempts_and_in_the_totals() -> None:
    earlier = _result(
        provider="claude",
        model="haiku",
        exit_code=3,
        tokens=TokenUsage(input=100, output=10, cached=40),
        cost_usd=0.5,
    )
    entry = step_entry(
        index=1, slot="run", role="r", step_dir="d", result=_result(), attempts=[earlier]
    )
    assert entry["attempts"] == [
        {
            "provider": "claude",
            "model": "haiku",
            "exit_code": 3,
            "tokens": {"input": 100, "output": 10, "fresh": None, "cached": 40, "thinking": None},
            "cost_usd": 0.5,
        }
    ]
    assert (entry["provider"], entry["tokens"], entry["cost_usd"]) == ("codex", TEN_TWO, 0.25)
    report = with_step(_report(), entry)
    assert report["tokens"] == {
        "input": 110,
        "output": 12,
        "fresh": None,
        "cached": 40,
        "thinking": None,
    }
    assert (report["tokens_complete"], report["cost_usd"], report["cost_complete"]) == (
        True,
        0.75,
        True,
    )


def test_a_step_without_attempts_records_an_empty_list() -> None:
    entry = step_entry(index=1, slot="run", role="r", step_dir="d", result=_result())
    assert entry["attempts"] == []


def test_an_unmeasured_spender_makes_the_totals_incomplete() -> None:
    entry = step_entry(
        index=1, slot="run", role="r", step_dir="d", result=_result(tokens=None, cost_usd=None)
    )
    report = with_step(_report(), entry)
    assert (report["tokens"], report["tokens_complete"]) == (None, False)
    assert (report["cost_usd"], report["cost_complete"]) == (None, False)


def test_a_refused_step_makes_tokens_incomplete() -> None:
    report = with_step(
        _report(), step_entry(index=1, slot="review", role="a", step_dir="d1", result=_result())
    )
    report = with_step(report, refused_step_entry(index=2, slot="review", role="b", step_dir="d2"))
    assert report["tokens"] == TEN_TWO
    assert report["tokens_complete"] is False
    assert report["steps"][1]["attempts"] == []


def test_an_older_report_and_step_without_the_new_keys_still_total() -> None:
    step = step_entry(index=1, slot="run", role="a", step_dir="d", result=_result())
    legacy_step = {key: value for key, value in step.items() if key != "attempts"}
    legacy = {k: v for k, v in _report().items() if k not in ("tokens", "tokens_complete")}
    report = with_step(legacy, legacy_step)
    assert report["tokens"] == TEN_TWO
    assert (report["tokens_complete"], report["cost_usd"]) == (True, 0.25)


def test_the_summary_line_prints_what_was_measured() -> None:
    report = dict(
        _report(),
        exit_code=0,
        cost_usd=0.37,
        cost_complete=True,
        tokens={
            "input": 8_300_000,
            "output": 5_800,
            "fresh": 900_000,
            "cached": 7_400_000,
            "thinking": None,
        },
        tokens_complete=True,
    )
    assert summary_line(report) == (
        "run 20260925T000000-aaaaaaaa: exit 0, cost 0.37 USD, input 8.3M (cached 7.4M), "
        "output 5.8k"
    )


def test_the_summary_line_omits_what_was_not_measured() -> None:
    report = dict(_report(), exit_code=3, cost_usd=None, cost_complete=False)
    report.update(tokens=None, tokens_complete=False)
    assert summary_line(report) == "run 20260925T000000-aaaaaaaa: exit 3"
    report = dict(_report(), exit_code=0, cost_usd=None, cost_complete=False)
    report.update(tokens={"input": 950, "output": None}, tokens_complete=True)
    assert summary_line(report) == "run 20260925T000000-aaaaaaaa: exit 0, input 950"


def test_the_summary_line_marks_incomplete_figures() -> None:
    report = dict(_report(), exit_code=1, cost_usd=0.37, cost_complete=False)
    report.update(tokens={"input": 12_000, "output": 300}, tokens_complete=False)
    assert summary_line(report) == (
        "run 20260925T000000-aaaaaaaa: exit 1, cost ≥ 0.37 USD (incomplete), "
        "input ≥ 12.0k, output ≥ 300 (incomplete)"
    )
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/unit/headless_agents/test_report.py -q`
Expected: FAIL at collection, `ImportError: cannot import name 'summary_line'`.

- [ ] **Step 3: Implement** in `report.py` (add `from collections.abc import Mapping, Sequence`):

```python
RUN_KEYS: Final = (
    "schema", "kind", "run_id", "target", "status", "exit_code", "verdict", "text",
    "repository", "base", "head", "branch", "lineage", "continues", "findings_from",
    "implement_providers", "commits", "failure_reason", "vendor_check", "cleanup", "pid",
    "started_at", "duration_seconds", "cost_usd", "cost_complete", "tokens",
    "tokens_complete", "steps",
)  # fmt: skip
STEP_KEYS: Final = (
    "index", "slot", "role", "dir", "provider", "model", "model_reported",
    "model_source", "effort", "timeout_seconds", "exit_code", "duration_seconds",
    "tokens", "cost_usd", "tools", "verdict", "attempts",
)  # fmt: skip
#: The ``TokenUsage`` fields a run total sums (spec 2026-10-13 usage §3.3).
TOKEN_FIELDS: Final = ("input", "output", "fresh", "cached", "thinking")
```

  `new_report`: add `tokens_complete=True` to its `report.update(...)`. `step_entry` gains
  the keyword `attempts: Sequence[RunResult] = ()` and the entry ends with
  `"attempts": [attempt_entry(attempt) for attempt in attempts],` after `"verdict": None`.
  `refused_step_entry`: `entry.update(index=index, slot=slot, role=role, dir=step_dir,
  exit_code=2, attempts=[])`. New functions and the new `with_step`:

```python
def attempt_entry(result: RunResult) -> dict[str, object]:
    """An earlier link of a chain that did not answer (spec 2026-10-13 usage §3.2)."""
    return {
        "provider": result.provider,
        "model": result.model,
        "exit_code": result.exit_code,
        "tokens": asdict(result.tokens) if result.tokens is not None else None,
        "cost_usd": result.cost_usd,
    }


def _spenders(steps: Sequence[Mapping[str, object]]) -> list[Mapping[str, object]]:
    """Every step and every attempt of a step: what a run total adds up."""
    spenders: list[Mapping[str, object]] = []
    for step in steps:
        spenders.append(step)
        attempts = step.get("attempts")
        if isinstance(attempts, list):
            spenders.extend(a for a in attempts if isinstance(a, Mapping))
    return spenders


def run_tokens(spenders: Sequence[Mapping[str, object]]) -> dict[str, int | None] | None:
    """Per field, the sum over the spenders that measured it; ``None`` when none did."""
    totals: dict[str, int | None] = dict.fromkeys(TOKEN_FIELDS)
    for spender in spenders:
        tokens = spender.get("tokens")
        if not isinstance(tokens, Mapping):
            continue
        for name in TOKEN_FIELDS:
            value = tokens.get(name)
            if isinstance(value, int) and not isinstance(value, bool):
                current = totals[name]
                totals[name] = value if current is None else current + value
    return None if all(value is None for value in totals.values()) else totals


def with_step(report: Mapping[str, object], step: Mapping[str, object]) -> dict[str, object]:
    """A copy of ``report`` with ``step`` appended and the run totals recomputed,
    attempts included (spec 2026-10-13 usage §3.3)."""
    previous = report.get("steps")
    steps = [*(previous if isinstance(previous, list) else []), dict(step)]
    spenders = _spenders(steps)
    costs = [s.get("cost_usd") for s in spenders]
    measured = [c for c in costs if isinstance(c, int | float) and not isinstance(c, bool)]
    updated = dict(report)
    updated["steps"] = steps
    updated["cost_usd"] = sum(measured) if measured else None
    updated["cost_complete"] = len(measured) == len(costs)
    updated["tokens"] = run_tokens(spenders)
    updated["tokens_complete"] = all(isinstance(s.get("tokens"), Mapping) for s in spenders)
    return updated


def _count(value: int) -> str:
    if value < 1000:
        return str(value)
    if value < 999_950:
        return f"{value / 1000:.1f}k"
    return f"{value / 1_000_000:.1f}M"


def _usd(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")


def summary_line(report: Mapping[str, object]) -> str:
    """The one line that closes a run on stderr (spec 2026-10-13 usage §3.3).

    An unmeasured figure is omitted, never printed as ``0``; an incomplete one reads as
    a lower bound (``≥``) and says so.
    """
    parts = [f"run {report.get('run_id')}: exit {report.get('exit_code')}"]
    cost = report.get("cost_usd")
    if isinstance(cost, int | float) and not isinstance(cost, bool):
        if report.get("cost_complete") is False:
            parts.append(f"cost ≥ {_usd(float(cost))} USD (incomplete)")
        else:
            parts.append(f"cost {_usd(float(cost))} USD")
    tokens = report.get("tokens")
    figures: list[str] = []
    if isinstance(tokens, Mapping):
        at_least = "≥ " if report.get("tokens_complete") is False else ""
        given, cached, produced = tokens.get("input"), tokens.get("cached"), tokens.get("output")
        if isinstance(given, int) and not isinstance(given, bool):
            figure = f"input {at_least}{_count(given)}"
            if isinstance(cached, int) and not isinstance(cached, bool):
                figure += f" (cached {at_least}{_count(cached)})"
            figures.append(figure)
        if isinstance(produced, int) and not isinstance(produced, bool):
            figures.append(f"output {at_least}{_count(produced)}")
        if figures and at_least:
            figures[-1] += " (incomplete)"
    return ", ".join(parts + figures)
```

  Add `"TOKEN_FIELDS"`, `"attempt_entry"`, `"run_tokens"` and `"summary_line"` to `__all__`
  (sorted).

- [ ] **Step 4: Run the tests** — `uv run pytest tests/unit/headless_agents/test_report.py tests/unit/headless_agents/test_show.py tests/unit/headless_agents/test_cli.py -q` → all pass.

- [ ] **Step 5: Lint and type-check** — `uv run ruff format src tests && uv run ruff check src tests && uv run mypy src` exit 0.

- [ ] **Step 6: Commit**

```sh
git add src/headless_agents/report.py
git add tests/unit/headless_agents/test_report.py
git commit -m "✨ feat(report): add attempts, run token totals and the run summary line"
```

---

### Task 7: The engine records attempts and closes each run with its summary

**Files:**
- Modify: `src/headless_agents/engine.py` — imports (61-64), `_run_links` (849-891),
  `_execute_write` (1133-1222: its `run_links` closure and `step_entry`), `_run_phase`
  (its `run_one` and the `step_entry` loop), `execute` (1487-1712: the three `return`s)
- Test: `tests/unit/headless_agents/test_engine_execute.py` (update `_Fake` 29-65, append)

**Interfaces:**
- Consumes: `step_entry(..., attempts=...)`, `summary_line` (Task 6).
- Produces: `_run_links(..., attempts: list[RunResult] | None = None) -> RunResult` (extends
  `attempts` with every link that ran before the final one); `_summarised(outcome: Outcome,
  say: Callable[[str], None]) -> Outcome`.

- [ ] **Step 1: Write the failing tests.** In `_Fake`, add two fields after
  `failure_text`: `tokens: TokenUsage | None = None` and `cost: float | None = 0.5`, and in
  its `RunResult(...)` use `tokens=self.tokens` and `cost_usd=self.cost`; import
  `from headless_agents.result import RunResult, TokenUsage`. Append:

```python
# ── usage (spec 2026-10-13 usage and budget §3.2, §3.3) ─────────────────────


def test_an_earlier_link_that_spent_lands_in_attempts_and_the_totals(world: World) -> None:
    world.roles('[r]\nchain = ["claude:haiku", "codex"]\n')
    world.fakes["claude"] = _Fake(
        "claude",
        code=3,
        tokens=TokenUsage(input=30004, output=531, fresh=10368, cached=19636, thinking=350),
        cost=0.0253,
    )
    world.fakes["codex"] = _Fake(
        "codex", tokens=TokenUsage(input=100, output=10, fresh=60, cached=40), cost=None
    )
    outcome = world.run("r")
    assert outcome.exit_code == 0
    assert outcome.final is not None and outcome.final.provider == "codex"
    report = json.loads((outcome.run_dir / "run.json").read_text())
    (step,) = report["steps"]
    assert (step["provider"], step["exit_code"], step["cost_usd"]) == ("codex", 0, None)
    assert step["tokens"] == {"input": 100, "output": 10, "fresh": 60, "cached": 40, "thinking": None}
    assert step["attempts"] == [
        {
            "provider": "claude",
            "model": "haiku",
            "exit_code": 3,
            "tokens": {
                "input": 30004,
                "output": 531,
                "fresh": 10368,
                "cached": 19636,
                "thinking": 350,
            },
            "cost_usd": 0.0253,
        }
    ]
    assert report["text"] == "the answer"
    assert report["tokens"] == {
        "input": 30104,
        "output": 541,
        "fresh": 10428,
        "cached": 19676,
        "thinking": 350,
    }
    assert report["tokens_complete"] is True
    assert (report["cost_usd"], report["cost_complete"]) == (0.0253, False)


def test_a_single_link_run_has_no_attempts(world: World) -> None:
    outcome = world.run("codex")
    report = json.loads((outcome.run_dir / "run.json").read_text())
    assert report["steps"][0]["attempts"] == []
    assert report["tokens"] is None and report["tokens_complete"] is False


def test_one_summary_line_closes_the_run(world: World) -> None:
    world.fakes["codex"] = _Fake(
        "codex", tokens=TokenUsage(input=1500, output=20, fresh=500, cached=1000)
    )
    outcome = world.run("codex")
    summaries = [line for line in world.said if line.startswith(f"run {outcome.run_id}:")]
    assert summaries == [
        f"run {outcome.run_id}: exit 0, cost 0.5 USD, input 1.5k (cached 1.0k), output 20"
    ]
    assert world.said[-1] == summaries[0]
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/unit/headless_agents/test_engine_execute.py -q -k "attempts or summary"`
Expected: FAIL — `step["attempts"] == []` instead of the claude entry, and no summary line.

- [ ] **Step 3: Implement** in `engine.py`. Import `summary_line` with the other
  `.report` names. `_run_links` gains the keyword `attempts: list[RunResult] | None = None`
  (after `slot`) and, right after `final = results[outcome.provider]`:

```python
    if attempts is not None:
        # Spec 2026-10-13 usage §3.2: every link that ran before the final one spent.
        attempts.extend(result for name, result in results.items() if name != outcome.provider)
```

  `execute` (read path): `earlier: list[RunResult] = []` before the `_run_links` call, pass
  `attempts=earlier`, and `step_entry(..., attempts=earlier)`. `_execute_write`: declare
  `earlier: list[RunResult] = []` before the `run_links` closure, pass `attempts=earlier` to
  its `_run_links`, and `attempts=earlier` to its `step_entry`. `_run_phase`: before
  `def run_one`, `attempts_of: dict[int, list[RunResult]] = {}`; inside `run_one`, after
  `index, slot, prompt = step`: `earlier: list[RunResult] = []` and
  `attempts_of[index] = earlier`, with `attempts=earlier` passed to `_run_links`; in the
  entries loop, `step_entry(..., attempts=attempts_of.get(index, []))`. Add:

```python
def _summarised(outcome: Outcome, say: Callable[[str], None]) -> Outcome:
    """Say the run summary line once, after all accounting (spec 2026-10-13 usage §3.3)."""
    say(summary_line(outcome.report))
    return outcome
```

  and wrap the three returns of `execute`: `return _summarised(_execute_review(...), say)`,
  `return _summarised(_execute_write(...), say)`, and the read path's
  `return _summarised(Outcome(...), say)`.

- [ ] **Step 4: Run the engine, review and write tests** — `uv run pytest tests/unit/headless_agents/test_engine_execute.py tests/unit/headless_agents/test_review.py tests/unit/headless_agents/test_implement.py tests/unit/headless_agents/test_write_flow.py tests/unit/headless_agents/test_cli.py -q` → all pass.

- [ ] **Step 5: Lint and type-check** — `uv run ruff format src tests && uv run ruff check src tests && uv run mypy src` exit 0.

- [ ] **Step 6: Commit**

```sh
git add src/headless_agents/engine.py
git add tests/unit/headless_agents/test_engine_execute.py
git commit -m "✨ feat(engine): count every attempted link and close each run with its summary"
```

---

### Task 8: `ha show` prints the run totals

**Files:**
- Modify: `src/headless_agents/show.py` — `render` (550-601), new `_totals_line` next to
  `format_tokens` (413-420)
- Test: `tests/unit/headless_agents/test_show.py` (append)

**Interfaces:**
- Consumes: `format_tokens`, `format_cost` (show.py 397-420); report keys `tokens`,
  `tokens_complete`, `cost_usd`, `cost_complete`.
- Produces: `_totals_line(report: Mapping[str, object]) -> str | None`; `render` adds
  `total   <tokens>  <cost>` after the step table when the report carries run totals.

- [ ] **Step 1: Write the failing tests** — append to `test_show.py`:

```python
# ── run totals (spec 2026-10-13 usage and budget §3.3) ──────────────────────


def test_the_run_totals_follow_the_steps(home: Home) -> None:
    report = home.report(
        tokens={"input": 30104, "output": 541, "fresh": None, "cached": None, "thinking": None},
        tokens_complete=True,
        cost_usd=0.0253,
        cost_complete=False,
    )
    lines = show.render(_shown(report, task="Explain what this repository does.")).splitlines()
    assert lines[3] == "total   in 30k out 541  $0.03 (incomplete)"
    assert lines[4] == "--- run ---"


def test_incomplete_tokens_are_marked(home: Home) -> None:
    report = home.report(
        tokens={"input": 1_500_000, "output": 3000},
        tokens_complete=False,
        cost_usd=None,
        cost_complete=False,
    )
    assert "total   in 1.5M out 3k (incomplete)  -" in show.render(_shown(report)).splitlines()


def test_a_report_without_run_totals_prints_no_total_line(home: Home) -> None:
    rendered = show.render(_shown(home.report())).splitlines()
    assert not any(line.startswith("total") for line in rendered)
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/unit/headless_agents/test_show.py -q -k "total"`
Expected: FAIL — `IndexError`/`AssertionError` on `lines[3]` (no total line yet); the
third test already passes.

- [ ] **Step 3: Implement** in `show.py`:

```python
def _totals_line(report: Mapping[str, object]) -> str | None:
    """``total   in 30k out 541  $0.03``: the run totals, attempts included (spec
    2026-10-13 usage §3.3); ``None`` for a report written before run totals existed."""
    complete = report.get("tokens_complete")
    if not isinstance(complete, bool):
        return None
    tokens = format_tokens(report.get("tokens"))
    if tokens != "-" and not complete:
        tokens += " (incomplete)"
    cost = format_cost(report.get("cost_usd"))
    if cost != "-" and report.get("cost_complete") is False:
        cost += " (incomplete)"
    return f"total   {tokens}  {cost}"
```

  In `render`, right after `lines.extend(_table(rows))`:

```python
    totals = _totals_line(report)
    if totals is not None:
        lines.append(totals)
```

- [ ] **Step 4: Run the tests** — `uv run pytest tests/unit/headless_agents/test_show.py tests/unit/headless_agents/test_cli.py -q` → all pass (the goldens carry no `tokens_complete`, so they are unchanged).

- [ ] **Step 5: Lint and type-check** — `uv run ruff format src tests && uv run ruff check src tests && uv run mypy src` exit 0.

- [ ] **Step 6: Commit**

```sh
git add src/headless_agents/show.py
git add tests/unit/headless_agents/test_show.py
git commit -m "✨ feat(show): print the run totals under the steps"
```

---

### Task 9: Documentation — per-rail measurement table, run.json keys, changelog

**Files:**
- Modify: `README.md` — after the `headless_agents.envelope.unwrap` paragraph (129-132);
  the run.json bullet (855-870)
- Modify: `CHANGELOG.md` — a `## Unreleased` section above `## 0.5.5 — 2026-10-13`

**Interfaces:**
- Consumes: the behaviour of Tasks 1-8.
- Produces: documentation only.

- [ ] **Step 1: README — measured usage by rail.** After the `envelope.unwrap` paragraph,
  insert:

```markdown
### Measured usage by rail

Each rail fills `RunResult.tokens` (and `cost_usd` where its CLI reports a cost) from the
output its own invocation wrote; a run that never launched measures nothing. `null` means
not measured, never zero, and ha ships no price table.

| Rail | Tokens | Cost (USD) | Read from |
|---|---|---|---|
| claude | measured, every model summed (`input` counts cache reads and cache creation) | measured (`total_cost_usd`) | the JSON envelope claude prints on every run |
| codex | measured | none (`cost_usd: null`) | the single `turn.completed` of the `--json` stream; a stream with more is not measured |
| agy | measured | none (`cost_usd: null`) | the final `result` event of the stream-json output |
| opencode | measured | measured | every `step_finish` part, a re-emitted part counted once |
| openai-compat | measured when the API reports usage | when the router reports it (OpenRouter) | the response body |
| vibe | not measured | none | Vibe's streaming output carries no usage |

claude runs with `--output-format json`: without a schema its envelope is kept beside the
caller's log (`<answer_log>.envelope.json`, or `<raw_log>.envelope.json`) and the answer is
its `result`, written to `answer_log` or, for a raw-log-only caller, to
`<raw_log>.answer.txt` (the `report_path`); `raw_log` holds stderr only. A schema run keeps
`claude-result.json`.
```

- [ ] **Step 2: README — run.json.** In the **Runs** bullet: the key list becomes
  "... `duration_seconds`, `cost_usd`, `cost_complete`, `tokens`, `tokens_complete` and
  `steps`"; add after it: "`tokens` sums each token field over every step and every
  attempted link that measured it (`null` when none did), `tokens_complete` is false when
  a step or link measured none, and `cost_usd`/`cost_complete` include attempted links."
  The step sentence gains "`tools`, `attempts` (one `{provider, model, exit_code, tokens,
  cost_usd}` per chain link that ran before the one that answered) and `dir`". Add: "Every
  `ha run` ends with one stderr line, `ha: run <id>: exit <code>, cost <usd> USD, input
  <n> (cached <n>), output <n>`, omitting what was not measured and marking incomplete
  figures `≥ … (incomplete)`; `ha show` prints the same totals as its `total` line."

- [ ] **Step 3: CHANGELOG** — above `## 0.5.5 — 2026-10-13`:

```markdown
## Unreleased

### Added

- Usage on every CLI rail that reports it (spec `docs/specs/2026-10-13-usage-and-budget-design.md`,
  PR1): claude records tokens and cost, codex and agy record tokens (`cost_usd: null`);
  vibe stays unmeasured. `envelope.extract_claude`, `claude_usage`, `codex_usage` and
  `agy_usage`; `usage.StreamMark`.
- `run.json`: `tokens` and `tokens_complete` run totals; each step's `attempts` (the chain
  links that ran before the one that answered), counted in the totals and in `cost_usd`.
- One run summary line on stderr at the end of every `ha run`; a `total` line in `ha show`.

### Changed

- **breaking** for direct `run_claude` callers: claude always runs with
  `--output-format json`. Without a schema its stdout envelope lands in
  `<answer_log|raw_log>.envelope.json`, the answer (the envelope's `result`) in
  `answer_log` or `<raw_log>.answer.txt`, and `raw_log` holds stderr only; a raw-log-only
  `ClaudeProvider` run reports `<raw_log>.answer.txt` as `report_path`. An envelope path
  equal to one of the logs is a `ValueError`. `envelope.unwrap*` results are unchanged.
```

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/unit/headless_agents/test_readme_examples.py tests/unit/headless_agents/test_readme_synopsis.py -q` → all pass.
Run: `grep -c "^| vibe | not measured" README.md` → prints `1` (exit 0).
Run: `make ci` → green (lint, format, mypy, unit tests, build); then `rail check` → green.

- [ ] **Step 5: Commit**

```sh
git add README.md
git add CHANGELOG.md
git commit -m "📝 docs(usage): document measured usage per rail and the run totals"
```

---

## Deferred to the PR2 plan

Everything about caps (ticket fe73f648) is PR2 and is **not** implemented here:

- spec §3.4 `--max-cost` / `--max-input-tokens`, `roles.toml` `max_cost` /
  `max_input_tokens`, the `Role` fields and the resolution rule;
- spec §3.5 admission (static capabilities per rail, refusal with exit 2 before launch);
- spec §3.6 enforcement: the ledger, sequential capped panels, opencode incremental
  reading and 0.1 s polling, immediate SIGKILL, exit 7 (`BUDGET_EXIT_CODE`),
  `budget_exhausted` / `budget_unmeasured`, `RunResult.partial_text` and
  `RunResult.failure_reason`, the step keys `partial_answer`, `steps_after_crossing`,
  `started`, `failure_reason`, the run key `budget`, `FINAL_STATUSES`, lineage and
  `ha show` propagation;
- spec §3.7 exit code 7 in the run epilog and the README;
- amendments A1 (exit code 7, not 5) and A2 (D4's bound with polling);
- the PR2 half of spec §4.

The rail reviewer's notes on the spec, to carry into the PR2 plan:

- **F-17-1**: the spec's enumeration of the `budget` keys (§3.6, "`run.json`'s `budget`
  object") omits `spent_complete`, which §3.6 itself relies on ("`budget` gets
  `spent_complete: false`"). PR2 must include `spent_complete` in the `budget` object and
  its key-set test.
- **F-17-2**: PR2 must document `--max-cost`, `--max-input-tokens` and the `roles.toml` keys
  `max_cost` / `max_input_tokens` in the README, in the roles documentation and in the CLI
  help.

## Execution notes

- Task 0 is done by the orchestrating session (it records a live claude envelope); the
  worker starts at Task 1.
- Order is strict: 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 (each consumes the previous
  interfaces). Tasks 2 and 3 may swap; Task 5 needs Task 4.
- Copy every test verbatim. A worker without network cannot run a provider: every proof
  here is a unit test with a fake process or a recorded fixture.
- Release evidence (spec D10: one live run per measured rail) belongs to the release, not
  to this PR.
