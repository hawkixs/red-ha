# Usage on every rail and per-run budget caps — design

Date: 2026-10-13. Status: design approved by review, awaiting the operator; decisions approved by the operator (items 10, 13, 15,
16-22 below), design review by codex (role judge, gpt-6.1-sol): rounds 1-4 REQUEST_CHANGES, round 5 APPROVE; amendments A1-A3. Lot 0.5.6 of headless-agents. Answers
ticket 82f066ba (G5, tokens and cost on the claude, codex and agy rails, from
red-arena) and ticket fe73f648 (a per-run cost/token budget, from brain-v42).

## 1. Problem

**Measurement.** `RunResult` carries `tokens: TokenUsage | None` and `cost_usd`
(`result.py:33-66`), and `run.json` sums the step costs into `cost_usd` with a
`cost_complete` flag (`report.py:110-120`). Only two rails fill them:

| Rail | Tokens | Cost (USD) | When usage is known |
|---|---|---|---|
| opencode | measured (`opencode.py:679-711`, fresh/cached/output/thinking) | measured, sum of `part.cost` | after every agent step (`step_finish`) |
| openai_compat | measured (`openai_compat.py:114-169`) | when the router returns it | end of the single HTTP request |
| claude | `None` (`claude.py:775`) | never set | end of run, and only when a JSON schema asked for the envelope |
| codex | `None` (`codex.py:1554`) | the CLI reports none | `turn.completed` in the `--json` stream |
| agy | `None` (`agy.py:970`) | the CLI reports none | the final `result` event of the stream |
| vibe | `None` (`vibe.py:703-706`) | none | never: Vibe's streaming output carries no usage |

`envelope.py` already parses claude's `total_cost_usd` and `modelUsage`, codex's
`turn.completed` usage and agy's `usage` block (`envelope.py:90-201`), but no rail calls
it: it is a public helper for callers (`README.md:129`, `codex.py:1551-1553`). red-arena's
milestone M4 records cost per game and cannot while three CLI rails answer `None`.

**Budget.** On 2026-10-06 one PR judge on the opencode rail with
`openrouter/z-ai/glm-5.3-flash` read a 111 KB diff file by file and consumed 8.3 M input
tokens (7.4 M cached) for 0.37 USD, billed on the operator's OpenRouter key: the agent
re-sends its growing context on each read, so cost scales with the number of reads and
nothing bounds it. The ticket asks for `--max-cost` and/or `--max-input-tokens`, per run
and per role, a clean stop with the partial answer, and the cost on the end-of-run line.

Success: every rail whose CLI reports usage fills `tokens` (and `cost_usd` where the CLI
reports a cost); a capped run on a rail that can enforce the cap stops at the step that
reaches it, records why and what it spent, and never falls through to another link; a
cap ha cannot enforce is refused before anything starts.

## 2. Decisions

Operator decisions, relayed by the coordination (brain decisions in brackets).

- **D1 — a cap ha cannot enforce mid-run is refused before launch** (item 10a,
  53cdab50). Exit 2 with a clear reason, never a run that might overspend. Rails that
  learn usage only at the end (claude, agy, codex, openai_compat) or never (vibe) cannot
  enforce a cap.
- **D2 — claude runs with `--output-format json` on every run** (13a, a2782485). Tokens
  and cost arrive at the end of the run; the answer is read from the envelope. Caps stay
  refused on claude (D1).
- **D3 — no price table** (15a, d4718948). On token-only rails (codex, agy) `cost_usd`
  stays `None` and the run is `cost_complete: false`; `--max-cost` on such a rail is
  refused before launch. No price table is shipped or maintained anywhere.
- **D4 — stop at the end of the step that reaches or crosses the cap** (item 16 = Q4 answered (a), 48801c57).
  Usage arrives per step, so the overshoot is bounded by one step; `run.json` records the
  cap, the amount spent and the overshoot. ha does not predict the next step's cost.
- **D5 — two caps, alone or together**: `--max-cost USD` and `--max-input-tokens N`,
  where N is the total input reported by the rail, cache reads included (the figure that
  reached 8.3 M). Per-role keys `max_cost` and `max_input_tokens` in `roles.toml`; the
  command line wins over the role (17a).
- **D6 — the cap covers the whole `ha run`** (18a). The spend of every step adds up and
  each step gets what is left. If any link of the run (a chain, a workflow step) cannot
  enforce a requested cap, the whole run is refused before launch (exit 2).
- **D7 — a stop on the cap has its own exit code**, `run.json` status
  `budget_exhausted`, partial answer kept, and the chain does not try the next link
  (19a). The operator chose "a new dedicated code, 5"; 5 is already the write run's
  "no change" (`write_flow.py:59`, `cli.py:102`) and 6 the review's "changes requested",
  so the dedicated code is **7**, the first free one (design review round 1). 7 is not
  one of the fallback codes 3 and 4 (`capability.py:64`), so `run_chain` stops there by
  construction (`chain.py:62-63`).
- **D8 — `run.json` gains run-level token totals** with a completeness flag, and the
  end-of-run stderr line shows cost and tokens (20a).
- **D9 — one spec, two pull requests**: PR1 measurement (G5), PR2 caps (fe73f648) (21a).
- **D10 — proof**: one unit test per rail on a recorded real output, plus one live smoke
  per measured rail at release (22a). Operator guidance with D4-D10: limits need not be
  strict; the only spend to protect is OpenRouter, so the live smoke billed through
  OpenRouter is one short call; subscription rails (claude, codex, agy) are free to use.

## 3. Design

Design choices below that are the author's, not the operator's, are flagged
**(author)**; the coordination relayed them and the operator can overturn them. Two
precisions of D4 and D7 forced by the code are recorded under Amendments.

### 3.1 One usage reader per rail (PR1)

Each rail measures from the output its own invocation wrote, through the parsers of
`envelope.py` split into a pure extraction step that both the public `unwrap_*` helpers
and the rails call. The public helpers keep their exact results (`test_envelope.py`
unchanged); a rail may aggregate differently only through its own aggregation step.

**Only this invocation's bytes.** A rail reads usage from the bytes its child wrote in
this invocation: the byte offset of each stream file is recorded when the child is
launched, and the reader starts there. A rail that returns before launching (refusal,
launch failure, an already expired deadline: `codex.py:1193`, `agy.py:660`) records
`tokens=None` without reading any file, so a reused log can never lend an earlier run's
usage. Measuring never changes an exit code or a failure classification.

- **claude** (D2). The argv always carries `--output-format json`; `--json-schema` is
  added only when a schema is asked, as today (`claude.py:163-165`).
  - **Where stdout goes.** stdout is the envelope, one JSON object per invocation. It is
    written to a file named after the log the caller chose for the answer: when the caller
    passes `answer_log`, the envelope is `<answer_log>.envelope.json`; when it passes only
    `raw_log`, it is `<raw_log>.envelope.json`. Ownership follows the caller's own log path, so two
    invocations with distinct logs never share an envelope, and two invocations given the
    same log path were already undefined (claude appends to its logs). ha truncates the
    envelope when the child starts and refuses, as a usage error, an envelope path equal
    to `raw_log` or `answer_log`. **Schema runs are the exception**: they keep their
    current directory-based `claude-result.json` (`RESULT_ENVELOPE_NAME`, `claude.py:61`,
    `claude.py:682`) with today's ownership, untouched by this lot; two schema runs whose
    report logs share a directory share it today and still do (out of scope).
  - **stderr** keeps its current destination (`raw_log`, or the separated stream of
    `claude.py:443` when an answer log exists), so the OTEL console stream that
    `tool_call_completed` (`claude.py:180-197`) and the fallback classification read is
    unchanged.
  - **Without a schema**, the answer text is the envelope's `result` string. It is
    written to `answer_log` when the caller passed one (the exported
    `run_claude(answer_log=...)` contract, text in a file, holds). A raw-log-only caller
    used to get the answer from `raw_log` (`claude.py:766`, `report_path=raw_log`); since
    `raw_log` now holds stderr only, ha writes the answer to `<raw_log>.answer.txt` and
    returns that path as `report_path`. When stdout is not a JSON object, the answer is
    stdout verbatim, as today, and usage is `None`. Measurement is independent of the
    answer: an envelope without a `result` string (a max-turns failure,
    `fixtures/structured/claude-2.1.283.max-turns.json` carries cost and full model
    usage) still yields its tokens and cost, while the failure classification and the
    absent `text` stay as today (the extraction already separates them,
    `envelope.py:110`).
  - **With a schema**, nothing changes: `structured_output` is required
    (`claude.py:551`) and a malformed envelope fails exactly as today (`claude.py:586`).
    The tolerance of the previous point never applies to a schema run.
  - **Normalisation.** The rail measures the whole invocation, every model of
    `modelUsage` summed (a run may call a second model): `input` = input + cache-read +
    cache-creation tokens, `cached` = cache-read tokens, `fresh` = `input - cached`
    (cache creation is fresh input), `output` = output tokens, `thinking` = the sum of
    `thinkingTokens` where reported (a recorded envelope carries 43,
    `fixtures/structured/claude-2.1.283.success.json:49`), `None` when no model reports
    it, never added again to `output`; `cost_usd` = `total_cost_usd`. This is the rail's
    own aggregation over the shared extraction; the public `unwrap_claude`, which reads
    one model and leaves cache creation out (`envelope.py:90-124`), keeps its results.
- **codex**. Tokens from `turn.completed` in the `--json` stream written to
  `events_log` (`codex.py:245`, `1243`): `input`, `cached` = `cached_input_tokens`,
  `fresh = input - cached`, `output`, `thinking` = `reasoning_output_tokens`. **Measured
  2026-10-13** on every codex events log under `~/.cache/ha/runs`: 1,029 logs hold
  exactly one `turn.completed`, none holds more (`codex exec` runs one turn; its tool
  iterations are items inside it, and the usage is the turn's total, e.g. 262,950 input
  tokens over 11 tool calls). The rail takes that single event. A stream with more than
  one `turn.completed` is not measured (`tokens=None`) until a recording shows its
  semantics: never a guessed sum. A completed turn followed by a failed turn or an
  error event keeps the completed turn's usage, the failure classification and the
  absent answer as today (`codex.py:1536`). `cost_usd` stays `None` (D3).
- **agy**. Tokens from the final `result` event of the stream-json output
  (`envelope.py:177-201`). `cost_usd` stays `None` (D3).
- **opencode, openai_compat**: unchanged; they are the reference.
- **vibe**: unchanged, `None`, stated in the README's per-rail table. Measuring Vibe
  needs its ACP mode, follow-up 1 of ticket 1cdf00d8, out of this lot.

### 3.2 Every attempted link counts (PR1)

A chain returns only its final link's result (`engine.py:884`, recorded as the role step
at `engine.py:1689`), yet an earlier link may have spent tokens before returning 3 or 4.
The step keeps the final link's provider, model, text and exit code (unchanged
contract) and gains `attempts`: one entry per earlier link, `{provider, model,
exit_code, tokens, cost_usd}`. The step's `tokens` and `cost_usd` stay the final link's;
the **run totals** (§3.3) add the attempts. Panel members are steps already.

### 3.3 Run totals, report schema and the final summary line (PR1)

`run.json` gains, in `RUN_KEYS` (`report.py:27-32`, the key set `show.py:157` filters
through):

- `tokens`: per `TokenUsage` field, the sum over every step and every attempt that
  measured that field; `null` when none did;
- `tokens_complete`: true when every step and attempt reported `tokens` (a refused step,
  exit 2, carries none and makes it false, like `cost_complete`);
- `cost_usd` and `cost_complete` keep their meaning and now include attempts;
- `budget` (PR2, §3.6): `null` on an uncapped run.

`STEP_KEYS` gains `attempts`, `partial_answer`, `steps_after_crossing`, `started` and
`failure_reason` (§3.6). Readers of older schema-1
reports treat every new key as absent (`null`, or `[]` for `attempts`); the schema
version stays 1 because nothing is removed or retyped.

After the last step and all accounting, ha prints **one run summary line** on stderr,
separate from the per-step lines (`engine.py:1165`, `1305`, `1681`), for example
`ha: run 20261013T…: exit 0, cost 0.37 USD, input 8.3M (cached 7.4M), output 5.8k`.
An unmeasured figure is omitted, never printed as `0`; an incomplete one is marked
(`cost ≥ 0.37 USD (incomplete)`). `ha show` prints the run totals.

### 3.4 Caps: options, roles, resolution (PR2)

- `ha run` gains `--max-cost USD` and `--max-input-tokens N`. Both must be finite and
  strictly positive (`--max-input-tokens` an integer); anything else is a usage error.
- `roles.toml` accepts `max_cost` and `max_input_tokens` (`roles.py` `_FIELDS`,
  validated like the options; a boolean is refused although TOML makes it an int). The
  `Role` dataclass carries them; model, effort and timeout resolution is untouched.
- **Resolution (author).** The run has one budget. Each cap is the command-line value
  when given; otherwise the **smallest** value any role of the run sets (a single role, a
  chain's role, every slot role of a workflow); otherwise no cap. Caps are run-level
  options, not role overrides, so they are accepted on workflows although ordinary role
  overrides are refused there (`engine.py:658`).

### 3.5 Caps: admission (PR2)

Each rail declares two static capabilities: usage reported **during** a run, and a
**cost** reported. Today only opencode reports usage during a run; claude, opencode and
openai_compat report a cost. During planning, before any registration or process,
`UsageError` (exit 2 through the existing path, `cli.py:1255`) refuses the run when a cap
is set and any link of any step (chain links, panel members, the judge, every workflow
step) runs on a rail that cannot report usage during the run, or when `--max-cost` is set
and such a link reports no cost. The message names the link, the cap and the reason. In
practice a capped run today is an opencode run.

### 3.6 Caps: enforcement (PR2)

**One ledger, one spender at a time (author).** A capped run keeps one budget ledger.
Its steps run **one at a time**: a panel phase whose members run concurrently today
(`engine.py:1308`) runs them sequentially when the run is capped, each in its own
isolated seat with its own `events.jsonl` and `stderr.log`, exactly as today; uncapped
runs keep their concurrency. With a single spender, D4's one-step bound applies to the
whole run, and no member can spend while the remaining allowance is unknown.

**opencode incremental reading.** The polling loop opencode already runs
(`opencode.py:1042-1063`) also reads the stream appended since the last poll:

- an incomplete trailing line stays in a buffer until its newline arrives;
- a persistent map `part id → latest step_finish part` lives for the whole step, so a part
  re-emitted across polls replaces its earlier version instead of adding to it (the
  last-version-wins rule of `step_finish_parts`, `opencode.py:662-676`, made
  incremental);
- the step's spend is the total over that map; the ledger receives the delta since the
  step's previous contribution;
- when the run is capped, the poll interval is 0.1 s instead of `QUOTA_POLL_SECONDS`
  (0.5 s), and the termination on a cap sends SIGKILL to the process group at once,
  without the grace a deadline gets (`capability.py:286`): a budget stop has no answer
  to wait for;
- after the child exits (naturally or killed), a final drain reads what remains and
  settles the ledger.

**Crossing the cap.** When the ledger's total reaches or crosses a cap, ha kills the
running capped process group. The bound of D4 is therefore "the crossing step, plus any
step that completes within one poll interval of it". An agent step is a full model call
that takes seconds, so in practice the stop lands on the crossing step; the record never
hides a second one: `budget` carries the exact spend and overshoot, and the step entry
counts the `step_finish` parts seen after the crossing (`steps_after_crossing`).

**Outcome of the step and of the run.** The outcome depends only on the measured spend,
never on whether ha's kill or the child's own exit was observed first:

- A step whose measured spend (after the final drain) reached or crossed a cap ends with
  exit **7** (`BUDGET_EXIT_CODE`, new, in `capability.py`), status `budget_exhausted`,
  whether ha killed it or it exited on its own; a crossing found only by the final drain
  is the same case. Whatever text the stream holds, complete or not, is its
  `partial_answer` (below), never its `text`.
- A step whose spend stayed under every cap is classified exactly as today: natural
  termination alone does not make an answer, the ordinary failure validation applies
  (`opencode.py:1095`).
- After a step ends at or over a cap, **no later step, link or phase starts** (a workflow
  step, the next panel member, the review judge), and the run ends `budget_exhausted`,
  exit 7. A step that is never started appears in `run.json` with `exit_code: null`,
  `started: false` and its `failure_reason`.

**Missing measurement under a cap.** When a capped step's completed `step_finish` lacks
the measure a cap needs (no `tokens` block for `--max-input-tokens`, no `cost` for
`--max-cost`; a measured zero is a measure), the remaining allowance is unknowable: ha
kills that step, exit 1, classified reason `budget_unmeasured`, starts no later step,
link or phase, and ends the run failed. The spend known so far is kept as a **lower
bound**: the step's `cost_complete`/`tokens_complete` contribution is false, `budget`
gets `spent_complete: false`, `spent_*` read as "at least", and every `overshoot_*` is
`null` (unknown), never `0`; the run summary line says `cost ≥ … (incomplete)`.

**In-flight calls.** The same lower-bound rule applies when, after the final drain, a
`step_start` has no matching `step_finish` (a call ha killed in flight, the case
`opencode.py:718` already recognises): its consumption is unknown, so `spent_complete` is
false and the overshoots are `null`. A known crossing still ends the step with exit 7; the
in-flight call only makes the record incomplete. Without a cap the same unmatched start
makes `tokens_complete`/`cost_complete` false and nothing else changes. 1 is not a fallback code, so no chain link
follows. Uncapped runs keep today's tolerant reading (`opencode.py:693`).

**The partial answer.** After a kill on the cap, the stream's text so far is extracted
(today only the normal completion extracts it, `opencode.py:1093`). It travels as
`RunResult.partial_text` (new field, `None` everywhere else), lands in the step entry as
`partial_answer`, and is never `text`: `run_record`'s rule that a nonzero exit has no
answer (`run_record.py:43`) is unchanged, and no review verdict or finding is ever read
from it. Display:

- `ha run` (plain output, `cli.py:910`) prints nothing on stdout for exit 7, as for any
  failure, and prints the partial on stderr after the line
  `ha: budget exhausted; partial answer follows (not an answer):`;
- `ha run --json` carries it in the step's `partial_answer`;
- `ha show` (`show.py:595`) prints, for each step that has one, a block headed
  `partial answer (budget exhausted, not an answer)`, panel members included; the review
  text stays cleared as for any review without a verdict (`show.py:301`).

**Reasons travel with the result.** `RunResult` gains `failure_reason: str | None`
(`None` on every path that does not set it), set by the budget meter only, to
`budget_exhausted` or `budget_unmeasured`. The step entry records it as `failure_reason`
(added to `STEP_KEYS`), and the run-level `failure_reason` takes the deciding step's
budget reason where today it would be null (read runs, `engine.py:1727`) or the generic
`step_failed` (reviews, `engine.py:1339`; writes, `write_flow.py:1193`). A safety reason
(tripwire, hook, moved HEAD) or an interruption keeps precedence over both.

**Propagation.**

- `budget_exhausted` joins `FINAL_STATUSES` (`runs.py:27`), so the registry and lineages
  (`lineage.py:148`) record it, and `ha show` derives it from authoritative state
  (`show.py:267`).
- Workflows: an implement step stopped on the cap keeps today's handling of a failed
  step's residue (committed, provenance recorded, `--continue` possible:
  `write_flow.py:1133`); only the exit code and status change. A review whose member or
  judge was stopped or never started has no verdict.
- **Precedence.** A tripwire, a hook failure, a moved HEAD or a Ctrl-C (130) keeps its
  current exit code and status: a safety failure or an interruption outranks a budget
  stop.
- `run.json`'s `budget` object: `{"max_cost", "max_input_tokens", "spent_cost",
  "spent_input_tokens", "overshoot_cost", "overshoot_input_tokens"}`; a cap not set is
  `null` and so is its overshoot; `overshoot = max(0, spent - cap)`; costs are JSON
  numbers in USD like `cost_usd`, token counts integers. On a capped run that never
  reached its cap the object is present with zero overshoots.

### 3.7 Exit codes, documented

7 is added to the general run documentation: the run epilog (`cli.py:95-110`), the
provider example (`README.md:76`) and the summary (`README.md:872`): the run stopped at
its budget cap, partial answer kept apart, no fallback. The HTTP-provider outcome table
(`README.md:445`) gains only that a cap on an HTTP link is refused before launch, exit 2.

## 4. Testing

TDD for every behaviour, red then green.

**PR1 — measurement**

- Recorded outputs, one per rail, captured from the installed CLIs and scrubbed of paths
  and ids, under `tests/unit/headless_agents/fixtures/` (path as amended by A3): claude envelopes without and with a schema, one
  with cache-creation and cache-read tokens and one with two models in `modelUsage`; a
  codex `--json` stream; an agy stream-json output; an opencode stream with re-emitted
  parts; an openai_compat response body; and a vibe stream showing no usage. A test per
  rail feeds its fixture through the rail's result path and pins every `TokenUsage` field
  and `cost_usd` (vibe: `None`, by design), and the public `unwrap_*` results are pinned
  unchanged on the same fixtures.
- claude: the argv carries `--output-format json` with and without a schema; on recorded
  envelopes the answer text, `answer_log`, `structured_output` and the schema run's
  `claude-result.json` keep their current values; a raw-log-only caller gets the answer
  in `<raw_log>.answer.txt` as `report_path` and stderr alone in `raw_log`; two
  concurrent non-schema invocations with distinct logs in one directory keep distinct
  envelopes; thinking tokens summed when present and `None` when absent, single- and
  multi-model; an
  envelope path aliasing a log is refused; a non-JSON stdout gives the verbatim text and
  `None` usage without a schema and fails as today with one; `tool_call_completed` still
  reads the OTEL stream.
- claude without a schema: a max-turns envelope without `result` records its tokens and
  cost, no `text`, the same failure classification, and its usage lands in `attempts`
  and the run totals when a fallback link follows.
- codex and agy: a reused events log followed by a refusal, a launch failure and an
  expired deadline records `tokens=None` and the same exit code as today; a codex stream
  with two `turn.completed` records `tokens=None`; a completed turn followed by a failed
  turn keeps the measured usage, no answer and the same failure classification.
- Attempts and totals: a chain whose first link returns 3 after spending puts its usage
  in `attempts` and in the run totals, final answer and provider unchanged; per-field
  sums, `None` stays `None`, a refused step makes `tokens_complete` false; an older
  `run.json` without the new keys reads; the run summary line prints measured figures,
  omits unmeasured ones and marks incomplete ones.

**PR2 — caps**

- Options and roles: valid and invalid values (zero, negative, NaN, infinity, boolean,
  non-integer tokens), the CLI override, the smallest role value across a workflow, a
  role with unknown keys still refused.
- Admission: each refusal on each rail, a chain whose second link only is unable, a
  review panel and an implement workflow; exit 2 and nothing registered or started.
- Enforcement, with a fake opencode child writing `step_finish` parts over time: a part
  re-emitted across polls counted once; a line split across two polls; two steps
  completing inside one poll interval (both counted, `steps_after_crossing` = 1); the kill
  is immediate (no grace); a child that exits on its own past the cap and one ha kills at
  the same measured spend both end exit 7 with the same record; a crossing found only by
  the final drain; a natural failure under the caps keeps today's classification;
  the `budget` object exact; `partial_answer` kept and `text` absent; a chain whose first
  link hits the cap does not start the second; absent, malformed and partial usage under
  a cap give `budget_unmeasured` and stop the run, while a measured zero does not; a valid
  finish followed by one missing cost or tokens yields `spent_complete: false`, null
  overshoots, incomplete totals in `--json` and in the summary line; a crossing finish,
  then a `step_start` killed before its finish, ends exit 7 with `spent_complete: false`
  and null overshoots in `--json`, the summary line and `ha show`; the
  same inputs uncapped behave as today.
- Panels: a capped review runs its members one after another, each in its own seat with
  readable `events.jsonl` and `stderr.log`; when the first member crosses the cap the
  second and the judge are not started; an uncapped review still runs concurrently.
- Display: `ha run` plain output prints nothing on stdout and the labelled partial on
  stderr for exit 7; `--json` carries `partial_answer`; `ha show` prints each step's
  labelled partial, panel members included, and no verdict.
- Reasons: `budget_exhausted` and `budget_unmeasured` reach the step entry and the
  run-level `failure_reason` of a plain run, a review and a write, as `ha show` displays
  them; a skipped step carries its reason; a tripwire keeps precedence.
- Propagation: registry entry, lineage member and `ha show` all read `budget_exhausted`;
  an implement step stopped on the cap commits its residue and can be continued; a
  tripwire during a capped run keeps its own exit code.

**Release evidence (D10).** Mandatory, each one recorded or the release proof stays
pending (never skipped):

- claude, codex and agy: one short live run each on their subscriptions, showing
  measured tokens (and a cost on claude);
- opencode: one short live run and one capped run with a tiny `--max-input-tokens`
  showing the exit 7 stop, both on the opencode-go subscription; while its quota is
  exhausted, this item stays pending;
- openai_compat: the release's **only OpenRouter-billed call**, one short prompt to an
  OpenRouter flash model (operator rule ca12005a), showing measured tokens and cost;
- the regression replay of what must not regress: isolated panel seats with readable
  logs, classified failure reasons, `AI_AGENT` on every worker (`capability.py:88`),
  roles choosing model, effort and timeout, two parallel codex runs, a `--continue`
  lineage, opencode's token breakdown, and exit 2 refused before start.

## 5. Non-goals

- A USD estimate for token-only rails, or any price table (D3).
- Caps on claude, codex, agy, vibe or openai_compat (D1); stopping inside a model call.
- Vibe measurement through ACP (ticket 1cdf00d8, follow-up 1).
- A cap on output tokens, or a spend ledger across runs.
- Changing `envelope.py`'s public results.

## 6. Success criteria

- claude, codex and agy runs record non-`None` tokens from a real CLI; claude records a
  cost; codex and agy record `cost_usd: null` with `cost_complete: false`; vibe stays
  unmeasured and says so.
- `run.json` carries run token totals including fallback attempts, `tokens_complete`
  and, on capped runs, the `budget` object; the run summary line shows cost and tokens
  where measured.
- A cap on any link that cannot enforce it is refused, exit 2, before anything is
  registered or started. A capped opencode run whose spend reaches a cap ends with exit 7,
  `budget_exhausted` in the registry, lineage and `ha show`, the `budget` record and the
  labelled partial answer; no fallback link, later step or judge runs.
- Every release-evidence item recorded; `make ci` and `rail check` green.

## Amendments

### A1 — exit code 7, not 5 (design review, round 1)

The operator chose "a new dedicated code, 5" (19a). 5 is already the write run's "no
change" (`write_flow.py:59`, `cli.py:102`, `README.md:874`) and 6 the review's "changes
requested"; reusing 5 would make an ordinary write without changes indistinguishable from
a budget stop, and moving "no change" would break every caller that reads it. The budget
stop takes **7**, the first free code, with the meaning 19a chose (dedicated, no
fallback). Relayed to the coordination on 2026-10-13, which kept it under 48801c57 and
reported it to the operator; the operator can overturn it.

### A2 — D4's bound with polling (design review, rounds 1-2)

ha learns a step's usage only when opencode writes its `step_finish`, and notices it at
the next poll. The bound of D4 is stated in §3.6 as "the crossing step, plus any step that
completes within one poll interval of it", with capped polling at 0.1 s, an immediate
kill, sequential spenders and the exact overshoot recorded. The operator's guidance with
48801c57 ("pas besoin d'être strict sur les limites") covers this precision; reported to
the coordination with A1.

### A3 — agy's input excludes cache reads (plan, PR1)

§3.1 reads agy's tokens from the final `result` event. On 15 recorded result events,
`total_tokens` equals `input_tokens + output_tokens` every time while `cache_read_tokens`
reaches 15 times `input_tokens`: agy's `input_tokens` excludes cache reads, unlike codex
(whose `cached_input_tokens` is a subset of `input_tokens`). The agy rail therefore
normalises like the others, so that `input` is the total input D5's cap measures:
`input` = `input_tokens` + `cache_read_tokens`, `cached` = `cache_read_tokens`, `fresh` =
`input_tokens`, `output` = `output_tokens` (agy counts thinking inside it), `thinking` =
`thinking_tokens`. The public `unwrap_agy` keeps its current result. Recorded fixtures live
under `tests/unit/headless_agents/fixtures/` with the existing ones, not under
`tests/fixtures/usage/` as §4 first said.
