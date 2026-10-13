# Usage on every rail, PR2 (budget caps) — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `ha run --max-cost USD` and `--max-input-tokens N` (and the `roles.toml` keys
`max_cost` / `max_input_tokens`) put one budget on a whole run. A cap no link of the run
can enforce is refused before anything is registered or started (exit 2); a capped
opencode run stops at the step that reaches its cap with exit 7, status
`budget_exhausted`, the `budget` record in `run.json` and the partial answer kept apart
from any answer; no fallback link, later step, panel member or judge starts. This is PR2
(ticket fe73f648) of the spec; PR1 (measurement, §3.1-§3.3) is merged and is built on, not
redone.

**Architecture:** a new rail-agnostic `budget.py` holds the cap validators, `Caps` and the
run's one `Ledger` (settled spend + the running step's totals, the `budget` record).
`registry.py` declares which rails report usage during a run and which report a cost, and
`cap_refusal` says why a rail cannot enforce a cap. `engine.plan()` resolves the run's
`Caps` (command line, else the smallest role value) and admits them on every link of every
step. `execute()` creates the ledger, hands it to every link through `RunSpec.budget`, runs
a capped panel's members one at a time, and records `budget`, the reasons and the
never-started steps. On the opencode rail a `StepMeter` reads the event stream
incrementally at every 0.1 s poll (buffered partial lines, last version of each
`step_finish` part wins), kills the process group at once (`kill_process_group`, no grace)
when the spend reaches a cap or a finish lacks its measure, and after a final read decides
exit 7 / exit 1 from the measured spend only. `RunResult` gains `partial_text`,
`failure_reason` and `usage_complete`; `run.json` gains the run key `budget` and the step
keys `partial_answer`, `steps_after_crossing`, `started`, `failure_reason`,
`usage_complete`; `budget_exhausted` becomes a final status; `ha run` and `ha show`
label the partial answer. No price table anywhere (D3).

**Tech Stack:** Python 3.12.7+, uv, pytest, ruff (line length 100, `ruff format`), mypy
(`disallow_untyped_defs` on `src`); no new dependency.

**Spec:** `docs/specs/2026-10-13-usage-and-budget-design.md` (approved; PR2 = §2 D1-D7,
§3.4-§3.7, the PR2 half of §4, amendments A1 and A2 — read them first) and the two review
notes carried by `docs/plans/2026-10-13-usage-measurement.md` ("Deferred to the PR2
plan"): F-17-1 (`spent_complete` is a key of `budget`) and F-17-2 (document the options,
the help and the `roles.toml` keys, not only exit 7).

## Global Constraints

- **D1/D6 admission.** A run is capped when `--max-cost` or `--max-input-tokens` is given
  or any role of the run sets `max_cost` / `max_input_tokens`. A capped run is refused in
  `plan()` (a `UsageError`, exit 2 through `cli.py`'s existing path, before any
  registration, lock or process) when any link of any step — chain links, every review
  member and the judge, an implement step — runs on a rail that does not report usage
  during a run (every rail but opencode), or when `max_cost` is set and the link reports no
  cost. The message names the role, the link, the cap and the reason, and ends
  `; nothing ran`.
- **D5/§3.4 values.** `max_cost` is a finite number of USD greater than zero;
  `max_input_tokens` an integer greater than zero (a float such as `1000.0` is refused). A
  boolean is refused for both, though TOML and Python make it an int. Input tokens are the
  rail's total input, cache reads included (opencode: `tokens.input + tokens.cache.read`).
- **Resolution.** Each cap is the command line's when given, even when larger than a
  role's; else the smallest value any role of the run sets (the role, or every slot role of
  a review); else none. Caps live on `Request`, not `Overrides`: a workflow accepts them.
  Model, effort and timeout resolution is untouched.
- **D4/A2 enforcement.** One ledger per run; a capped run's steps spend one at a time (a
  review's members run sequentially, each in its own seat; uncapped panels keep their
  concurrency). opencode polls every `BUDGET_POLL_SECONDS = 0.1` when capped
  (`QUOTA_POLL_SECONDS = 0.5` otherwise) and kills with `kill_process_group` (SIGKILL to the
  group, no `TERMINATION_GRACE_SECONDS`). Reaching a cap is enough (`>=`).
- **D7/A1 outcome.** A step whose measured spend after the final read reached a cap exits
  `7` (`capability.BUDGET_EXIT_CODE`), `failure_reason: "budget_exhausted"`, whoever ended
  the child; its stream text is `RunResult.partial_text`, never `text`. A capped finish
  without its measure exits `1`, `failure_reason: "budget_unmeasured"`. Under every cap the
  step is classified exactly as today. 7 is not in `FALLBACK_EXIT_CODES`: a chain stops
  there by construction (`chain.py:192`).
- **Precedence.** A tripwire (the rail's `settle_run`, the write flow's `tripwire`), a
  hook, a moved HEAD or a Ctrl-C keeps its own code and status: the opencode rail sets no
  budget reason and no partial once `settle_run` reports tampering, and the write flow's
  safety outcomes return before any budget handling.
- **Records.** `run.json` `budget` is `null` uncapped, else exactly
  `{max_cost, max_input_tokens, spent_cost, spent_input_tokens, spent_complete,
  overshoot_cost, overshoot_input_tokens}` (F-17-1). A cap not set is `null` and so is its
  overshoot; a capped measure starts at `0`, an unwatched one stays `null` until reported;
  `overshoot = max(0, spent - cap)`; when the spend is only a lower bound
  (`spent_complete: false`) both overshoots are `null`. Step keys appended after
  `attempts`: `partial_answer`, `steps_after_crossing`, `started`, `failure_reason`,
  `usage_complete`. A never-started step has `exit_code: null`, `started: false`, the
  deciding reason, and is left out of the run totals. `budget_exhausted` joins
  `FINAL_STATUSES`. Schema version stays 1; `result.json` (`RunResult.to_dict`) is
  unchanged.
- **No price table** (D3): a cost is what the rail reported, never estimated.
- Strict TDD, unit tests only, no provider and no network. `make ci` green at every commit.
- English everywhere; public repository: no private hostnames, paths or ids in code, tests
  or docs.
- **For the worker:** copy every test verbatim and APPEND new tests at the END of the named
  test file; add only the imports each task names. Line numbers below are those of `main`
  at 8780486; earlier tasks shift them, so locate each edit by the quoted code.

## Review Focus

- **A `step_finish` part re-emitted across polls** (opencode re-emits a part on every
  update): it must replace its earlier version, never add to it — counting twice would
  stop a run that never reached its cap. Pinned in Task 6 (meter unit test) and Task 7
  (a real child re-emitting across two polls).
- **A crossing the meter sees only after the child already exited** (the child wrote its
  last finish and exited inside one poll): the outcome must still be exit 7 with the same
  record as a killed child, never a success. Pinned in Task 7 (`_FakeProcess` that exits
  in its first wait; own exit vs kill at the same spend).
- **A measured zero next to an absent measure**: `cost: 0` is a measure and never stops a
  run; an absent cost stops a `--max-cost` run but not a `--max-input-tokens`-only run.
  Pinned in Task 6.
- **A never-started step in the run totals**: it spent nothing, so it must not turn the
  totals incomplete, while a refused step (exit 2) still does (PR1). Pinned in Task 4.
- **A tripwire on a step that also crossed its cap**: the tripwire's exit 1 and reason win;
  no `budget_exhausted`, no partial answer. Pinned in Task 7 (rail) and Task 10 (write).

## Plan decisions where the spec is silent (for the operator)

- **A per-step lower-bound marker.** §3.6 makes a step's `cost_complete`/`tokens_complete`
  contribution false while keeping its spend, but §3.3's step key list has no carrier: the
  plan adds `RunResult.usage_complete` and the step key `usage_complete` (a sixth new step
  key), which `with_step` reads.
- **A never-started step is left out of the run totals** (it spent nothing); otherwise
  every budget stop with a skipped judge would read "incomplete". A refused step (exit 2)
  keeps PR1's rule and gets `started: true` (it reached its start).
- **A crossing and a missing measure on one step:** a known crossing wins (exit 7); the
  missing measure only makes the record a lower bound, as §3.6 rules for a call in flight.
- **What a "partial" usage is:** under `--max-input-tokens` a finish needs `tokens.input`
  and `tokens.cache.read` as non-negative integers; under `--max-cost` a non-negative
  finite `cost`. A measure no cap watches may be absent.
- **`spent_*` of an unwatched measure:** `spent_cost` on a tokens-only budget is the
  reported cost sum, `null` when no step reported one (never `0`); likewise
  `spent_input_tokens` on a cost-only budget.
- **A write stopped on its cap prints its header** (run id, branch, diffstat, patch) on
  stdout, as every failed write does today; no answer text goes there. §3.6's "nothing on
  stdout" is met by the read run and the review.
- **`RunResult`'s new fields stay out of `result.json`** (`to_dict` unchanged): the step
  entry of `run.json` carries them.

---

## File structure

| File | Responsibility |
|---|---|
| `src/headless_agents/budget.py` (create, Task 1) | cap validators, `Caps`, `Ledger`, reasons |
| `src/headless_agents/capability.py` (modify) | `BUDGET_EXIT_CODE = 7`, `kill_process_group` |
| `src/headless_agents/roles.py`, `registry.py` (modify) | role keys; rail capabilities, `cap_refusal` |
| `src/headless_agents/engine.py` (modify) | `Request`/`Plan` caps, admission, ledger, sequential capped panels, records |
| `src/headless_agents/cli.py` (modify) | options, help, epilog, the labelled partial |
| `src/headless_agents/result.py`, `report.py` (modify) | `RunResult` fields, step/run keys, `skipped_step_entry`, `budget_line` |
| `src/headless_agents/runs.py`, `show.py` (modify) | final status; budget, reasons and partials in `ha show` |
| `src/headless_agents/spec.py`, `providers/opencode.py` (modify) | `RunSpec.budget`; `StepMeter`, enforcement |
| `src/headless_agents/write_flow.py` (modify) | a write's budget stop |
| `README.md`, `CHANGELOG.md` (modify) | documentation |

---

### Task 1: `budget.py` — cap values, `Caps`, the run's `Ledger`; exit code 7 and the immediate kill

**Files:**
- Create: `src/headless_agents/budget.py`
- Modify: `src/headless_agents/capability.py` — after `TERMINATION_GRACE_SECONDS` (29),
  after `INVALID_USAGE_EXIT_CODE` (69), after `terminate_process_group` (273-303, end of
  file)
- Test: `tests/unit/headless_agents/test_budget.py` (create);
  `tests/unit/headless_agents/test_capability.py` (append after line 161)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `BUDGET_EXHAUSTED: Final = "budget_exhausted"`, `BUDGET_UNMEASURED: Final = "budget_unmeasured"`, `BUDGET_REASONS: Final = frozenset({...})`
  - `cost_cap(value: object) -> float` and `input_tokens_cap(value: object) -> int` (both raise `ValueError` with the rule as message)
  - `@dataclass(frozen=True) class Caps` with `max_cost: float | None = None`, `max_input_tokens: int | None = None`, `capped -> bool` (property), `describe() -> str`
  - `@dataclass class Ledger(caps: Caps)` with `complete: bool = True`, `steps_after_crossing: int | None = None`, properties `spent_cost -> float | None`, `spent_input_tokens -> int | None`, `crossed -> bool`, methods `measure(*, cost: float | None, input_tokens: int | None) -> None`, `settle() -> None`, `record() -> dict[str, object]`
  - `capability.BUDGET_EXIT_CODE = 7`, `capability.KILL_REAP_SECONDS = 1.0`, `capability.kill_process_group(process: subprocess.Popen[str]) -> None`

- [ ] **Step 1: Write the failing tests** — create `tests/unit/headless_agents/test_budget.py`:

```python
"""The run's budget: cap values, the ledger and its ``budget`` object (spec 2026-10-13
usage and budget §3.4, §3.6; review note F-17-1)."""

from __future__ import annotations

import math

import pytest

from headless_agents.budget import (
    BUDGET_EXHAUSTED,
    BUDGET_REASONS,
    BUDGET_UNMEASURED,
    Caps,
    Ledger,
    cost_cap,
    input_tokens_cap,
)

BUDGET_KEYS = {
    "max_cost",
    "max_input_tokens",
    "spent_cost",
    "spent_input_tokens",
    "spent_complete",
    "overshoot_cost",
    "overshoot_input_tokens",
}


@pytest.mark.parametrize("value", [0.25, 1, 1e-9])
def test_a_cost_cap_is_a_finite_positive_number(value: float) -> None:
    assert cost_cap(value) == float(value)
    assert isinstance(cost_cap(value), float)


@pytest.mark.parametrize("value", [0, 0.0, -1, math.nan, math.inf, -math.inf, True, "1", None])
def test_anything_else_is_not_a_cost_cap(value: object) -> None:
    with pytest.raises(ValueError, match="finite number of USD greater than zero"):
        cost_cap(value)


def test_an_int_too_large_for_a_float_is_not_a_cost_cap() -> None:
    with pytest.raises(ValueError, match="finite number"):
        cost_cap(10**400)


def test_an_input_token_cap_is_a_positive_integer() -> None:
    assert input_tokens_cap(1000) == 1000


@pytest.mark.parametrize("value", [0, -5, 1000.0, 1.5, True, math.nan, "1000", None])
def test_anything_else_is_not_an_input_token_cap(value: object) -> None:
    with pytest.raises(ValueError, match="whole number of input tokens greater than zero"):
        input_tokens_cap(value)


def test_the_reasons_are_the_specs() -> None:
    assert (BUDGET_EXHAUSTED, BUDGET_UNMEASURED) == ("budget_exhausted", "budget_unmeasured")
    assert BUDGET_REASONS == {"budget_exhausted", "budget_unmeasured"}


def test_caps_say_whether_the_run_is_capped_and_name_themselves() -> None:
    assert not Caps().capped and Caps().describe() == "no cap"
    assert Caps(max_cost=0.5).describe() == "max_cost 0.5 USD"
    both = Caps(max_cost=0.5, max_input_tokens=1000)
    assert both.capped and both.describe() == "max_cost 0.5 USD and max_input_tokens 1000"


def test_a_capped_run_that_spent_nothing_records_zero_and_no_overshoot() -> None:
    record = Ledger(Caps(max_input_tokens=1000)).record()
    assert set(record) == BUDGET_KEYS
    assert record == {
        "max_cost": None,
        "max_input_tokens": 1000,
        "spent_cost": None,
        "spent_input_tokens": 0,
        "spent_complete": True,
        "overshoot_cost": None,
        "overshoot_input_tokens": 0,
    }


def test_a_measure_replaces_the_running_steps_figures() -> None:
    ledger = Ledger(Caps(max_cost=1.0))
    ledger.measure(cost=0.25, input_tokens=100)
    ledger.measure(cost=0.5, input_tokens=300)
    assert (ledger.spent_cost, ledger.spent_input_tokens) == (0.5, 300)
    assert not ledger.crossed and ledger.steps_after_crossing is None


def test_settled_steps_add_up_and_the_next_step_gets_what_is_left() -> None:
    ledger = Ledger(Caps(max_cost=1.0))
    ledger.measure(cost=0.5, input_tokens=None)
    ledger.settle()
    ledger.measure(cost=0.25, input_tokens=None)
    assert ledger.spent_cost == 0.75 and not ledger.crossed
    ledger.measure(cost=0.5, input_tokens=None)
    assert ledger.spent_cost == 1.0
    assert ledger.crossed, "reaching the cap is enough (D4)"
    assert ledger.steps_after_crossing == 0


def test_the_record_carries_the_exact_spend_and_overshoot() -> None:
    ledger = Ledger(Caps(max_cost=0.25, max_input_tokens=1000))
    ledger.measure(cost=0.375, input_tokens=1500)
    ledger.settle()
    assert ledger.record() == {
        "max_cost": 0.25,
        "max_input_tokens": 1000,
        "spent_cost": 0.375,
        "spent_input_tokens": 1500,
        "spent_complete": True,
        "overshoot_cost": 0.125,
        "overshoot_input_tokens": 500,
    }


def test_an_incomplete_spend_reads_at_least_and_its_overshoots_are_unknown() -> None:
    ledger = Ledger(Caps(max_cost=0.25, max_input_tokens=1000))
    ledger.measure(cost=0.5, input_tokens=2000)
    ledger.complete = False
    ledger.settle()
    record = ledger.record()
    assert (record["spent_cost"], record["spent_input_tokens"]) == (0.5, 2000)
    assert record["spent_complete"] is False
    assert (record["overshoot_cost"], record["overshoot_input_tokens"]) == (None, None)


def test_an_unwatched_measure_stays_null_until_reported() -> None:
    ledger = Ledger(Caps(max_input_tokens=1000))
    ledger.measure(cost=None, input_tokens=10)
    assert ledger.spent_cost is None
    ledger.measure(cost=0.125, input_tokens=10)
    assert ledger.record()["spent_cost"] == 0.125
    assert ledger.record()["overshoot_cost"] is None
```

  Append at the end of `tests/unit/headless_agents/test_capability.py` (its imports
  already cover `os`, `subprocess`, `sys`, `time`, `pytest` and `capability`):

```python
# ── budget stops (spec 2026-10-13 usage and budget §3.6, D7, A1) ────────────


def test_the_budget_code_is_7_and_never_a_fallback() -> None:
    assert capability.BUDGET_EXIT_CODE == 7
    assert capability.BUDGET_EXIT_CODE not in capability.FALLBACK_EXIT_CODES


def _group_ignoring_sigterm() -> subprocess.Popen[str]:
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal, subprocess, sys, time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "subprocess.Popen([sys.executable, '-c', "
            "'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "time.sleep(60)']); "
            "time.sleep(60)",
        ],
        text=True,
        start_new_session=True,
    )


@pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX")
def test_kill_process_group_kills_at_once_without_a_grace() -> None:
    process = _group_ignoring_sigterm()
    time.sleep(0.3)  # let the child spawn its own child
    started = time.monotonic()
    capability.kill_process_group(process)
    elapsed = time.monotonic() - started
    assert elapsed < capability.TERMINATION_GRACE_SECONDS / 2, f"took {elapsed:.2f}s"
    assert process.returncode is not None
    with pytest.raises(ProcessLookupError):
        os.killpg(process.pid, 0)


def test_kill_process_group_tolerates_an_already_gone_process() -> None:
    process = subprocess.Popen([sys.executable, "-c", "pass"], text=True, start_new_session=True)
    process.wait()
    capability.kill_process_group(process)
    assert process.returncode == 0
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/unit/headless_agents/test_budget.py tests/unit/headless_agents/test_capability.py -q`
Expected: FAIL — `test_budget.py` at collection with `ModuleNotFoundError: No module named
'headless_agents.budget'`; the three new `test_capability.py` tests with `AttributeError:
module 'headless_agents.capability' has no attribute 'BUDGET_EXIT_CODE'` / `'kill_process_group'`.

- [ ] **Step 3: Implement.** Create `src/headless_agents/budget.py`:

```python
"""Per-run budget caps (spec 2026-10-13 usage and budget §3.4-§3.6).

A capped run keeps ONE :class:`Ledger`. Its steps spend one at a time, every step adds
to it, and each step gets what the earlier ones left. The only rail that reports usage
during a run (opencode) measures its running step into the ledger as each
``step_finish`` part arrives; the engine reads the ledger to stop the run and to record
its ``budget`` object. A cost is what the rail reported, never an estimate: ha ships no
price table (D3).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Final

#: The step stopped because the run's measured spend reached or crossed a cap (exit 7).
BUDGET_EXHAUSTED: Final = "budget_exhausted"
#: A capped step's finished model call lacked the measure its cap needs (exit 1).
BUDGET_UNMEASURED: Final = "budget_unmeasured"
#: The failure reasons only the budget meter sets (``RunResult.failure_reason``).
BUDGET_REASONS: Final = frozenset({BUDGET_EXHAUSTED, BUDGET_UNMEASURED})

_COST_RULE: Final = "must be a finite number of USD greater than zero"
_TOKENS_RULE: Final = "must be a whole number of input tokens greater than zero"


def cost_cap(value: object) -> float:
    """``value`` as a cost cap in USD; :class:`ValueError` unless it is a finite number
    greater than zero. A boolean is refused, although TOML and Python make it an int."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(_COST_RULE)
    try:
        number = float(value)
    except OverflowError:
        raise ValueError(_COST_RULE) from None
    if not math.isfinite(number) or number <= 0:
        raise ValueError(_COST_RULE)
    return number


def input_tokens_cap(value: object) -> int:
    """``value`` as an input-token cap; :class:`ValueError` unless it is an integer greater
    than zero. A boolean and a float (even ``1000.0``) are refused."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(_TOKENS_RULE)
    return value


@dataclass(frozen=True)
class Caps:
    """The run's caps; ``None`` = not capped on that measure."""

    max_cost: float | None = None
    max_input_tokens: int | None = None

    @property
    def capped(self) -> bool:
        return self.max_cost is not None or self.max_input_tokens is not None

    def describe(self) -> str:
        """``max_cost 0.5 USD and max_input_tokens 1000``: how a refusal names the caps."""
        parts: list[str] = []
        if self.max_cost is not None:
            parts.append(f"max_cost {self.max_cost:g} USD")
        if self.max_input_tokens is not None:
            parts.append(f"max_input_tokens {self.max_input_tokens}")
        return " and ".join(parts) or "no cap"


def _add_cost(settled: float | None, running: float | None) -> float | None:
    if settled is None and running is None:
        return None
    return (settled or 0.0) + (running or 0.0)


def _add_tokens(settled: int | None, running: int | None) -> int | None:
    if settled is None and running is None:
        return None
    return (settled or 0) + (running or 0)


@dataclass
class Ledger:
    """The spend of one capped run, settled step by step (spec §3.6).

    A capped measure starts at zero; a measure no cap watches stays ``None`` until a
    step reports it (absent is not zero). ``complete`` turns false, for good, when a
    step's spend is only a lower bound (a finish without the measure, a call killed in
    flight). ``steps_after_crossing`` is ``None`` until the spend reaches a cap, then
    counts the ``step_finish`` parts the meter saw after the one that crossed.
    """

    caps: Caps
    complete: bool = True
    steps_after_crossing: int | None = None
    _settled_cost: float | None = field(default=None, init=False)
    _settled_input: int | None = field(default=None, init=False)
    _running_cost: float | None = field(default=None, init=False)
    _running_input: int | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if self.caps.max_cost is not None:
            self._settled_cost = 0.0
        if self.caps.max_input_tokens is not None:
            self._settled_input = 0

    @property
    def spent_cost(self) -> float | None:
        return _add_cost(self._settled_cost, self._running_cost)

    @property
    def spent_input_tokens(self) -> int | None:
        return _add_tokens(self._settled_input, self._running_input)

    @property
    def crossed(self) -> bool:
        """The spend reached or crossed a cap (D4: reaching it is enough)."""
        cost, tokens = self.spent_cost, self.spent_input_tokens
        caps = self.caps
        return (caps.max_cost is not None and cost is not None and cost >= caps.max_cost) or (
            caps.max_input_tokens is not None
            and tokens is not None
            and tokens >= caps.max_input_tokens
        )

    def measure(self, *, cost: float | None, input_tokens: int | None) -> None:
        """The running step's totals so far. They REPLACE the step's previous figures, so a
        re-read never counts twice; the ledger's total moves by the difference."""
        was_crossed = self.crossed
        self._running_cost, self._running_input = cost, input_tokens
        if not was_crossed and self.crossed:
            self.steps_after_crossing = 0

    def settle(self) -> None:
        """Fold the running step into the settled spend: the next step starts from it."""
        self._settled_cost, self._settled_input = self.spent_cost, self.spent_input_tokens
        self._running_cost = self._running_input = None

    def record(self) -> dict[str, object]:
        """``run.json``'s ``budget`` object (spec §3.6, review note F-17-1).

        A cap not set is ``null`` and so is its overshoot; ``overshoot = max(0, spent -
        cap)``; an incomplete spend reads "at least", and its overshoots are ``null``
        (unknown), never ``0``.
        """
        cost, tokens = self.spent_cost, self.spent_input_tokens
        max_cost, max_tokens = self.caps.max_cost, self.caps.max_input_tokens
        overshoot_cost = (
            max(0.0, cost - max_cost)
            if self.complete and max_cost is not None and cost is not None
            else None
        )
        overshoot_tokens = (
            max(0, tokens - max_tokens)
            if self.complete and max_tokens is not None and tokens is not None
            else None
        )
        return {
            "max_cost": max_cost,
            "max_input_tokens": max_tokens,
            "spent_cost": cost,
            "spent_input_tokens": tokens,
            "spent_complete": self.complete,
            "overshoot_cost": overshoot_cost,
            "overshoot_input_tokens": overshoot_tokens,
        }


__all__ = [
    "BUDGET_EXHAUSTED",
    "BUDGET_REASONS",
    "BUDGET_UNMEASURED",
    "Caps",
    "Ledger",
    "cost_cap",
    "input_tokens_cap",
]
```

  In `capability.py`, after `TERMINATION_GRACE_SECONDS = 5.0` add:

```python
#: How long :func:`kill_process_group` waits for the killed group to be gone.
KILL_REAP_SECONDS = 1.0
```

  after `INVALID_USAGE_EXIT_CODE = 2` add:

```python
# The run stopped because its measured spend reached or crossed its budget cap
# (spec 2026-10-13 usage and budget, D7 and amendment A1): a dedicated code, the
# first free one (5 is a write's "no change", 6 a review's "changes requested").
# Never a fallback code: a chain stops on it by construction, and no later step
# starts.
BUDGET_EXIT_CODE = 7
```

  and at the end of the file, after `terminate_process_group`:

```python
def kill_process_group(process: subprocess.Popen[str]) -> None:
    """SIGKILL the agent and every child it spawned, at once: no grace period.

    A budget stop has no answer to wait for (spec 2026-10-13 usage and budget
    §3.6), unlike a deadline, which :func:`terminate_process_group` lets end in
    its grace. Requires ``start_new_session=True``, like that function.
    """
    process_group = process.pid
    try:
        os.killpg(process_group, signal.SIGKILL)
    except ProcessLookupError:
        process.poll()
        return
    deadline = time.monotonic() + KILL_REAP_SECONDS
    while time.monotonic() < deadline:
        process.poll()  # Reap the leader; a killed child may linger a moment.
        try:
            os.killpg(process_group, 0)
        except ProcessLookupError:
            break
        time.sleep(0.01)
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
```

- [ ] **Step 4: Run the tests** — `uv run pytest tests/unit/headless_agents/test_budget.py tests/unit/headless_agents/test_capability.py -q` → all pass.

- [ ] **Step 5: Lint and type-check** — `uv run ruff format src tests && uv run ruff check src tests && uv run mypy src` exit 0.

- [ ] **Step 6: Commit**

```sh
git add src/headless_agents/budget.py
git add src/headless_agents/capability.py
git add tests/unit/headless_agents/test_budget.py
git add tests/unit/headless_agents/test_capability.py
git commit -m "✨ feat(budget): add the run ledger, cap values and exit code 7"
```

---

### Task 2: `roles.toml` caps and the rails' budget capabilities

**Files:**
- Modify: `src/headless_agents/roles.py` — imports (38), `_FIELDS` (57-75), `Role` (90-112),
  `_role` (240-242 after the `language` check; 248-266 the `Role(...)` call)
- Modify: `src/headless_agents/registry.py` — imports (28), after `_MAX_PROMPT_BYTES` (63-70,
  before `PROBE_TIMEOUT_SECONDS` at 72), before `max_prompt_bytes` (103)
- Test: `tests/unit/headless_agents/test_roles.py` (append after 193),
  `tests/unit/headless_agents/test_registry.py` (imports 15-17, append after 457)

**Interfaces:**
- Consumes: `cost_cap`, `input_tokens_cap`, `Caps` (Task 1).
- Produces:
  - `Role.max_cost: float | None = None`, `Role.max_input_tokens: int | None = None` (last fields)
  - `registry.USAGE_DURING_RUN_RAILS: frozenset[str]` (`{"opencode"}`), `registry.COST_REPORTING_RAILS: frozenset[str]` (`{"claude", "opencode", *HTTP_PROVIDER_NAMES}`); module-level, read at call time so a test can monkeypatch them
  - `registry.reports_usage_during_run(name: str) -> bool`, `registry.reports_cost(name: str) -> bool` (both raise `UnknownProvider`)
  - `registry.cap_refusal(name: str, caps: Caps) -> str | None`

- [ ] **Step 1: Write the failing tests.** Append at the end of `test_roles.py`:

```python
# ── budget caps (spec 2026-10-13 usage and budget §3.4) ─────────────────────


def test_a_role_carries_its_budget_caps(tmp_path: Path) -> None:
    text = '[judge]\nprovider = "opencode"\nmax_cost = 0.5\nmax_input_tokens = 2000000\n'
    role = _load(tmp_path, text)["judge"]
    assert (role.max_cost, role.max_input_tokens) == (0.5, 2_000_000)
    assert {"max_cost", "max_input_tokens"} <= role.declared


def test_an_integer_cost_cap_is_a_number_of_usd(tmp_path: Path) -> None:
    role = _load(tmp_path, '[judge]\nprovider = "opencode"\nmax_cost = 2\n')["judge"]
    assert role.max_cost == 2.0 and isinstance(role.max_cost, float)


def test_a_role_without_caps_is_uncapped(tmp_path: Path) -> None:
    role = _load(tmp_path, '[rev]\nprovider = "opencode"\n')["rev"]
    assert (role.max_cost, role.max_input_tokens) == (None, None)
    assert (implicit_role("opencode").max_cost, implicit_role("opencode").max_input_tokens) == (
        None,
        None,
    )


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "true", '"0.5"'])
def test_an_invalid_cost_cap_is_refused(tmp_path: Path, value: str) -> None:
    with pytest.raises(
        RolesError, match=r"\[judge\] max_cost must be a finite number of USD greater than zero"
    ):
        _load(tmp_path, f'[judge]\nprovider = "opencode"\nmax_cost = {value}\n')


@pytest.mark.parametrize("value", ["0", "-3", "1000.0", "1.5", "true", "nan", '"1000"'])
def test_an_invalid_input_token_cap_is_refused(tmp_path: Path, value: str) -> None:
    with pytest.raises(
        RolesError,
        match=r"\[judge\] max_input_tokens must be a whole number of input tokens greater than zero",
    ):
        _load(tmp_path, f'[judge]\nprovider = "opencode"\nmax_input_tokens = {value}\n')


def test_a_misspelt_cap_is_still_an_unknown_field(tmp_path: Path) -> None:
    with pytest.raises(RolesError, match=r"\[judge\] unknown field 'max_costs'"):
        _load(tmp_path, '[judge]\nprovider = "opencode"\nmax_costs = 1\n')


def test_the_caps_leave_model_effort_and_timeout_resolution_alone(tmp_path: Path) -> None:
    text = (
        '[judge]\nprovider = "opencode"\nmodel = "opencode-go/m"\neffort = "high"\n'
        "timeout = 90\nmax_cost = 0.5\n"
    )
    role = _load(tmp_path, text)["judge"]
    assert (role.model, role.effort, role.timeout) == ("opencode-go/m", "high", 90.0)
```

  In `test_registry.py`, add `from headless_agents.budget import Caps` right after
  `from headless_agents import registry`, then append at the end:

```python
# ── rail capabilities for budget caps (spec 2026-10-13 usage and budget §3.5) ─


def test_only_opencode_reports_usage_during_a_run() -> None:
    during = [name for name in registry.PROVIDER_NAMES if registry.reports_usage_during_run(name)]
    assert during == ["opencode"]


def test_claude_opencode_and_the_http_providers_report_a_cost() -> None:
    reporting = {name for name in registry.PROVIDER_NAMES if registry.reports_cost(name)}
    assert reporting == {"claude", "opencode", *registry.HTTP_PROVIDER_NAMES}


def test_an_unknown_rail_has_no_capability() -> None:
    with pytest.raises(registry.UnknownProvider):
        registry.reports_usage_during_run("nobody")
    with pytest.raises(registry.UnknownProvider):
        registry.reports_cost("nobody")


@pytest.mark.parametrize("name", ["claude", "codex", "agy", "vibe", "openrouter", "openai-compat"])
def test_a_cap_on_a_rail_that_reports_usage_only_at_the_end_is_refused(name: str) -> None:
    caps = Caps(max_input_tokens=1000)
    assert registry.cap_refusal(name, caps) == (
        f"max_input_tokens 1000 cannot be enforced: {name} reports no usage during a run"
    )
    assert registry.cap_refusal(name, Caps(max_cost=0.5)) == (
        f"max_cost 0.5 USD cannot be enforced: {name} reports no usage during a run"
    )


def test_opencode_takes_either_cap_and_no_cap_is_never_refused() -> None:
    assert registry.cap_refusal("opencode", Caps(max_cost=0.5, max_input_tokens=1000)) is None
    assert registry.cap_refusal("codex", Caps()) is None


def test_a_cost_cap_on_a_rail_that_reports_no_cost_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No rail is like that today; the rule holds the day one is (spec §3.5)."""
    monkeypatch.setattr(registry, "USAGE_DURING_RUN_RAILS", frozenset({"opencode", "codex"}))
    assert registry.cap_refusal("codex", Caps(max_input_tokens=1000)) is None
    assert registry.cap_refusal("codex", Caps(max_cost=0.5)) == (
        "max_cost 0.5 USD cannot be enforced: codex reports no cost"
    )
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/unit/headless_agents/test_roles.py tests/unit/headless_agents/test_registry.py -q`
Expected: 28 FAIL — the role tests with `RolesError: ... unknown field 'max_cost'` (or
`'max_input_tokens'`, which the refusal tests do not match) or `AttributeError: 'Role'
object has no attribute 'max_cost'`; the registry tests with `AttributeError: module
'headless_agents.registry' has no attribute 'reports_usage_during_run'` (`'reports_cost'`,
`'cap_refusal'`, `'USAGE_DURING_RUN_RAILS'`). `test_a_misspelt_cap_is_still_an_unknown_field`
is the guard of spec §4 "a role with unknown keys still refused": it passes before and
after this task.

- [ ] **Step 3: Implement.** In `roles.py`, import the validators (before
  `from .context import ContextLevel`):

```python
from .budget import cost_cap, input_tokens_cap
from .context import ContextLevel
```

  Add `"max_cost",` and `"max_input_tokens",` after `"language",` in `_FIELDS`; add to
  `Role`, after `language: str | None = None`:

```python
    #: Budget caps (spec 2026-10-13 usage and budget §3.4): run-level, the smallest
    #: value any role of the run sets applies unless the command line gives one.
    max_cost: float | None = None
    max_input_tokens: int | None = None
```

  In `_role`, right after the `language` check (`raise entry.refuse(f"language must be one
  of ...")`):

```python
    max_cost: float | None = None
    if "max_cost" in table:
        try:
            max_cost = cost_cap(table["max_cost"])
        except ValueError as exc:
            raise entry.refuse(f"max_cost {exc}") from None
    max_input_tokens: int | None = None
    if "max_input_tokens" in table:
        try:
            max_input_tokens = input_tokens_cap(table["max_input_tokens"])
        except ValueError as exc:
            raise entry.refuse(f"max_input_tokens {exc}") from None
```

  and pass them to the `Role(...)` call after `language=language,`:

```python
        language=language,
        max_cost=max_cost,
        max_input_tokens=max_input_tokens,
    )
```

  In `registry.py`, add `from .budget import Caps` before `from .capability import
  scoped_environment`; before `PROBE_TIMEOUT_SECONDS = 10.0` add:

```python
#: Spec 2026-10-13 usage and budget §3.5: the rails that report usage DURING a run, the
#: only ones that can stop a capped run at the step that reaches its cap (D1). The others
#: learn usage at the end of a run (claude, codex, agy, the HTTP providers) or never (vibe).
USAGE_DURING_RUN_RAILS: frozenset[str] = frozenset({"opencode"})
#: The rails that report a cost at all; codex, agy and vibe report tokens only, and ha
#: ships no price table (D3).
COST_REPORTING_RAILS: frozenset[str] = frozenset({"claude", "opencode", *HTTP_PROVIDER_NAMES})
```

  and before `def max_prompt_bytes`:

```python
def reports_usage_during_run(name: str) -> bool:
    """Whether the rail reports its usage while it runs (spec 2026-10-13 usage §3.5)."""
    return _known(name) in USAGE_DURING_RUN_RAILS


def reports_cost(name: str) -> bool:
    """Whether the rail reports a cost in USD (spec 2026-10-13 usage §3.5)."""
    return _known(name) in COST_REPORTING_RAILS


def cap_refusal(name: str, caps: Caps) -> str | None:
    """Why the rail ``name`` cannot enforce ``caps``, or ``None`` when it can (D1)."""
    if not caps.capped:
        return None
    if not reports_usage_during_run(name):
        return f"{caps.describe()} cannot be enforced: {name} reports no usage during a run"
    if caps.max_cost is not None and not reports_cost(name):
        return f"max_cost {caps.max_cost:g} USD cannot be enforced: {name} reports no cost"
    return None
```

- [ ] **Step 4: Run the tests** — `uv run pytest tests/unit/headless_agents/test_roles.py tests/unit/headless_agents/test_registry.py tests/unit/headless_agents/test_engine_plan.py -q` → all pass.

- [ ] **Step 5: Lint and type-check** — `uv run ruff format src tests && uv run ruff check src tests && uv run mypy src` exit 0.

- [ ] **Step 6: Commit**

```sh
git add src/headless_agents/roles.py
git add src/headless_agents/registry.py
git add tests/unit/headless_agents/test_roles.py
git add tests/unit/headless_agents/test_registry.py
git commit -m "✨ feat(roles): accept max_cost and max_input_tokens and declare rail budget capabilities"
```

---

### Task 3: Plan the caps — options, resolution, admission before anything starts

**Files:**
- Modify: `src/headless_agents/engine.py` — imports (33-34, 48-56), `Request` (120-148),
  `Plan` (164-191), new `_request_caps`/`_capped` before `plan` (724), `plan` (724-764: the
  workflow `return` at 732 and the final `return Plan(...)` at 753-764)
- Modify: `src/headless_agents/cli.py` — imports (41), new `_max_cost`/`_max_input_tokens`
  before `_parser` (145), the `run` parser after `--max-tokens` (228), `_run`'s `Request(...)`
  (867-896)
- Modify: `README.md` — the synopsis line `       [--output-schema FILE]` (513): the
  synopsis test (`test_readme_synopsis.py`) fails as soon as the parser has an option the
  README lacks
- Test: `tests/unit/headless_agents/test_engine_plan.py` (imports 10-31, append after 620),
  `tests/unit/headless_agents/test_cli.py` (imports 11-18, append after 1548)

**Interfaces:**
- Consumes: `Caps`, `cost_cap`, `input_tokens_cap` (Task 1); `cap_refusal`, `Role.max_cost`,
  `Role.max_input_tokens` (Task 2).
- Produces:
  - `Request.max_cost: float | None = None`, `Request.max_input_tokens: int | None = None` (last fields)
  - `Plan.caps: Caps = Caps()` (last field)
  - `engine._request_caps(request: Request) -> Caps`, `engine._capped(planned: Plan) -> Plan`
  - `cli._max_cost(text: str) -> float`, `cli._max_input_tokens(text: str) -> int` (argparse types raising `argparse.ArgumentTypeError`); `args.max_cost`, `args.max_input_tokens`

- [ ] **Step 1: Write the failing tests.** In `test_engine_plan.py` change
  `from dataclasses import dataclass, field` to `from dataclasses import dataclass, field,
  replace` and add `from headless_agents.budget import Caps` before `from
  headless_agents.context import ContextBundle`; append at the end:

```python
# ── budget caps: resolution and admission (spec 2026-10-13 usage and budget §3.4, §3.5) ─


def _capped(env: Env, target: str, prompt: str | None = "task", **caps: object) -> Request:
    return replace(env.request(target, prompt), **caps)  # type: ignore[arg-type]


def _opencode_panel(env: Env, judge: str = "opencode") -> None:
    env.roles(
        '[oc-a]\nprovider = "opencode"\nmax_input_tokens = 5000\n\n'
        '[oc-b]\nprovider = "opencode"\nmax_input_tokens = 3000\nmax_cost = 0.75\n\n'
        f'[oc-judge]\nprovider = "{judge}"\nmax_input_tokens = 4000\n\n'
        '[oc-build]\nprovider = "opencode"\nwrite = true\n'
    )
    (env.config / "workflows.toml").write_text(
        '[oc-panel]\nshape = "review"\nreview = ["oc-a", "oc-b"]\njudge = "oc-judge"\n\n'
        '[oc-implement]\nshape = "implement"\nimplement = "oc-build"\n'
    )


def test_an_uncapped_run_plans_no_caps(env: Env) -> None:
    planned = plan(env.request("opencode", "task"))
    assert planned.caps == Caps() and not planned.caps.capped


def test_the_command_line_caps_the_run(env: Env, no_subprocess: list[object]) -> None:
    planned = plan(_capped(env, "opencode", max_cost=0.5, max_input_tokens=4000))
    assert planned.caps == Caps(max_cost=0.5, max_input_tokens=4000)


def test_a_roles_caps_apply_and_the_command_line_wins_even_when_larger(env: Env) -> None:
    env.roles('[oc]\nprovider = "opencode"\nmax_cost = 0.5\nmax_input_tokens = 4000\n')
    assert plan(env.request("oc", "task")).caps == Caps(max_cost=0.5, max_input_tokens=4000)
    planned = plan(_capped(env, "oc", max_input_tokens=9000))
    assert planned.caps == Caps(max_cost=0.5, max_input_tokens=9000)


def test_a_workflow_takes_the_smallest_cap_of_its_roles(env: Env) -> None:
    _opencode_panel(env)
    planned = plan(env.request("oc-panel", None))
    assert planned.caps == Caps(max_cost=0.75, max_input_tokens=3000)


def test_caps_are_run_options_a_workflow_accepts(env: Env) -> None:
    _opencode_panel(env)
    planned = plan(_capped(env, "oc-implement", "Add a flag.", max_cost=0.25))
    assert planned.caps == Caps(max_cost=0.25)
    reviewed = plan(_capped(env, "oc-panel", None, max_input_tokens=100))
    assert reviewed.caps == Caps(max_cost=0.75, max_input_tokens=100)


@pytest.mark.parametrize(
    ("field_name", "value", "flag"),
    [
        ("max_cost", 0, "--max-cost"),
        ("max_cost", -1.0, "--max-cost"),
        ("max_cost", float("nan"), "--max-cost"),
        ("max_cost", float("inf"), "--max-cost"),
        ("max_cost", True, "--max-cost"),
        ("max_input_tokens", 0, "--max-input-tokens"),
        ("max_input_tokens", -5, "--max-input-tokens"),
        ("max_input_tokens", 1.5, "--max-input-tokens"),
        ("max_input_tokens", True, "--max-input-tokens"),
    ],
)
def test_an_invalid_cap_is_a_usage_error(
    env: Env, no_subprocess: list[object], field_name: str, value: object, flag: str
) -> None:
    with pytest.raises(UsageError, match=f"^{flag} .*: must be a"):
        plan(_capped(env, "opencode", **{field_name: value}))


@pytest.mark.parametrize("provider", ["claude", "codex", "agy", "vibe", "openrouter"])
def test_a_cap_on_a_rail_that_cannot_enforce_it_is_refused_before_anything(
    env: Env, no_subprocess: list[object], provider: str
) -> None:
    (env.config / "models.toml").write_text(
        'claude = "c"\ncodex = "x"\nagy = "g"\nvibe = "v"\nopenrouter = "o"\n'
    )
    with pytest.raises(
        UsageError,
        match=(
            f"^role {provider}, link {provider}: max_input_tokens 1000 cannot be enforced: "
            f"{provider} reports no usage during a run; nothing ran$"
        ),
    ):
        plan(_capped(env, provider, max_input_tokens=1000))
    with pytest.raises(UsageError, match=f"max_cost 0.5 USD cannot be enforced: {provider}"):
        plan(_capped(env, provider, max_cost=0.5))
    assert not env.state.exists(), "nothing registered"


def test_a_chain_whose_second_link_only_is_unable_is_refused(env: Env) -> None:
    env.roles('[pair]\nchain = ["opencode", "codex"]\n')
    with pytest.raises(UsageError, match="^role pair, link codex: max_cost 1 USD cannot be"):
        plan(_capped(env, "pair", max_cost=1.0))
    assert plan(env.request("pair", "task")).caps == Caps(), "uncapped, the chain still plans"


def test_a_role_cap_on_a_rail_that_cannot_enforce_it_refuses_every_run_of_it(env: Env) -> None:
    env.roles('[rev]\nprovider = "codex"\nmax_cost = 1\n')
    with pytest.raises(UsageError, match="^role rev, link codex: max_cost 1 USD cannot be"):
        plan(env.request("rev", "task"))


def test_a_review_whose_judge_only_is_unable_is_refused(
    env: Env, no_subprocess: list[object]
) -> None:
    _opencode_panel(env, judge="codex")
    with pytest.raises(UsageError, match="^role oc-judge, link codex: .* cannot be enforced"):
        plan(env.request("oc-panel", None))


def test_an_implement_workflow_on_an_unable_rail_is_refused(env: Env) -> None:
    _workflows(env)
    with pytest.raises(UsageError, match="^role implementer, link codex: max_input_tokens 10 "):
        plan(_capped(env, "build", "Add a flag.", max_input_tokens=10))
```

  In `test_cli.py` add `import argparse` before `import io`; append at the end:

```python
# ── budget caps (spec 2026-10-13 usage and budget §3.4, §3.5; review note F-17-2) ─


def _run_help() -> str:
    parser = cli._parser()
    commands = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return commands.choices["run"].format_help()


def test_the_run_parser_takes_both_caps() -> None:
    args = cli._parser().parse_args(
        ["run", "opencode", "task", "--max-cost", "0.25", "--max-input-tokens", "4000"]
    )
    assert (args.max_cost, args.max_input_tokens) == (0.25, 4000)
    bare = cli._parser().parse_args(["run", "opencode", "task"])
    assert (bare.max_cost, bare.max_input_tokens) == (None, None)


def test_the_run_help_documents_both_caps() -> None:
    text = " ".join(_run_help().split())
    assert "--max-cost USD" in text and "--max-input-tokens N" in text
    assert "cache reads included" in text and "exit 7" in text


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--max-cost", "0"),
        ("--max-cost", "-1"),
        ("--max-cost", "nan"),
        ("--max-cost", "inf"),
        ("--max-cost", "abc"),
        ("--max-input-tokens", "0"),
        ("--max-input-tokens", "-3"),
        ("--max-input-tokens", "1.5"),
        ("--max-input-tokens", "1e6"),
    ],
)
def test_an_invalid_cap_exits_2_and_nothing_runs(world: _World, flag: str, value: str) -> None:
    code, out, err = world.run("run", "opencode", flag, value, "task")
    assert code == 2 and out == ""
    assert f"argument {flag}: {value!r}:" in err
    assert world.registry().run_ids() == [] and world.fakes == {}


def test_a_cap_on_a_rail_that_cannot_enforce_it_exits_2_before_anything(world: _World) -> None:
    code, out, err = world.run("run", "codex", "--max-cost", "0.5", "task")
    assert code == 2 and out == ""
    assert err == (
        "ha: role codex, link codex: max_cost 0.5 USD cannot be enforced: codex reports no "
        "usage during a run; nothing ran\n"
    )
    assert world.registry().run_ids() == [] and world.fakes == {}
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/unit/headless_agents/test_engine_plan.py tests/unit/headless_agents/test_cli.py -q`
Expected: 35 FAIL — the plan tests with `TypeError: ... unexpected keyword argument
'max_cost'` (or `'max_input_tokens'`) from `replace`, or `AttributeError: 'Plan' object has
no attribute 'caps'`; the CLI tests with `ha: unrecognized arguments: --max-cost ...`
(exit 2 but the wrong message) or `AttributeError: 'Namespace' object has no attribute
'max_cost'`.

- [ ] **Step 3: Implement.** In `engine.py` add `from .budget import Caps, cost_cap,
  input_tokens_cap` before `from .capability import scoped_environment`, and `cap_refusal,`
  to the `from .registry import (...)` list after `PROVIDER_NAMES,`. At the end of `Request`,
  after `output_schema`:

```python
    #: ``--max-cost USD`` and ``--max-input-tokens N`` (spec 2026-10-13 usage and budget
    #: §3.4): run-level caps, not role overrides, so a workflow takes them too.
    max_cost: float | None = None
    max_input_tokens: int | None = None
```

  At the end of `Plan`, after `findings_from: str | None = None`:

```python
    #: The run's one budget (spec 2026-10-13 usage and budget §3.4), admitted on every
    #: link of every step (§3.5); ``Caps()`` when the run is not capped.
    caps: Caps = Caps()
```

  Before `def plan(request: Request) -> Plan:` add:

```python
def _request_caps(request: Request) -> Caps:
    """The caps the command line gives, validated here too: a library caller passes the
    engine's gates like the CLI (spec 2026-10-13 usage §3.4)."""
    try:
        max_cost = cost_cap(request.max_cost) if request.max_cost is not None else None
    except ValueError as exc:
        raise UsageError(f"--max-cost {request.max_cost!r}: {exc}") from None
    try:
        max_input_tokens = (
            input_tokens_cap(request.max_input_tokens)
            if request.max_input_tokens is not None
            else None
        )
    except ValueError as exc:
        raise UsageError(f"--max-input-tokens {request.max_input_tokens!r}: {exc}") from None
    return Caps(max_cost=max_cost, max_input_tokens=max_input_tokens)


def _capped(planned: Plan) -> Plan:
    """``planned`` with the run's one budget, admitted (spec 2026-10-13 usage §3.4, §3.5).

    Each cap is the command line's when given, else the smallest value any role of the
    run sets -- the role, or every slot role of a review -- else none. A cap is refused,
    before anything is registered or started (D1, D6), when any link of any step runs on
    a rail that cannot enforce it.
    """
    given = _request_caps(planned.request)
    roles = [slot.role for slot in planned.panel] or [planned.role]
    caps = Caps(
        max_cost=(
            given.max_cost
            if given.max_cost is not None
            else min((r.max_cost for r in roles if r.max_cost is not None), default=None)
        ),
        max_input_tokens=(
            given.max_input_tokens
            if given.max_input_tokens is not None
            else min(
                (r.max_input_tokens for r in roles if r.max_input_tokens is not None),
                default=None,
            )
        ),
    )
    for role in roles:
        for provider in role.providers:
            refusal = cap_refusal(provider, caps)
            if refusal is not None:
                raise UsageError(f"role {role.name}, link {provider}: {refusal}; nothing ran")
    return replace(planned, caps=caps)
```

  In `plan`, wrap both returns: `return _capped(_plan_workflow(request, workflow, config))`,
  and the final one:

```python
    return _capped(
        Plan(
            request=request,
            role=role,
            models=models,
            model_sources={name: source for name, (_, source) in resolved.items()},
            prompt=prompt,
            mcp=mcp,
            environment=_environment(request.environ, mcp),
            run_dir=_run_dir(request),
            state=state_dir(request.environ, home=request.home),
            task=prompt,
        )
    )
```

  In `cli.py` add `from .budget import cost_cap, input_tokens_cap` before `from .capability
  import INVALID_USAGE_EXIT_CODE`; before `def _parser()` add:

```python
def _max_cost(text: str) -> float:
    """``--max-cost USD`` (spec 2026-10-13 usage and budget §3.4)."""
    try:
        return cost_cap(float(text))
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{text!r}: a cost cap is a finite number of USD greater than zero"
        ) from None


def _max_input_tokens(text: str) -> int:
    """``--max-input-tokens N`` (spec 2026-10-13 usage and budget §3.4)."""
    try:
        return input_tokens_cap(int(text))
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{text!r}: an input-token cap is a whole number greater than zero"
        ) from None
```

  after `run.add_argument("--max-tokens", ...)`:

```python
    run.add_argument(
        "--max-cost",
        type=_max_cost,
        metavar="USD",
        help="stop the run at the step whose measured cost reaches USD; exit 7, the partial "
        "answer kept apart (every link must report usage during the run: opencode)",
    )
    run.add_argument(
        "--max-input-tokens",
        type=_max_input_tokens,
        metavar="N",
        help="stop the run at the step whose total input tokens, cache reads included, "
        "reach N; exit 7, the partial answer kept apart (opencode links only)",
    )
```

  and in `_run`, after `output_schema=output_schema,` in `Request(...)`:

```python
        output_schema=output_schema,
        max_cost=args.max_cost,
        max_input_tokens=args.max_input_tokens,
    )
```

  In `README.md`, the synopsis line `       [--output-schema FILE]` becomes
  `       [--output-schema FILE] [--max-cost USD] [--max-input-tokens N]` (the rest of
  the documentation is Task 11).

- [ ] **Step 4: Run the tests** — `uv run pytest tests/unit/headless_agents/test_engine_plan.py tests/unit/headless_agents/test_cli.py tests/unit/headless_agents/test_readme_synopsis.py -q` → all pass.

- [ ] **Step 5: Lint and type-check** — `uv run ruff format src tests && uv run ruff check src tests && uv run mypy src` exit 0.

- [ ] **Step 6: Commit**

```sh
git add src/headless_agents/engine.py
git add src/headless_agents/cli.py
git add README.md
git add tests/unit/headless_agents/test_engine_plan.py
git add tests/unit/headless_agents/test_cli.py
git commit -m "✨ feat(engine): resolve and admit budget caps before anything starts"
```

---

### Task 4: `RunResult` and `run.json` — the budget keys, never-started steps, lower bounds

**Files:**
- Modify: `src/headless_agents/result.py` — `RunResult` fields (65-66) and `to_dict`'s
  docstring (69-75)
- Modify: `src/headless_agents/report.py` — `RUN_KEYS`/`STEP_KEYS` (27-38), `step_entry`
  (73-105), `refused_step_entry` (108-113), `_spenders` (127-135), `with_step` (153-167),
  new `budget_line` before `write_report` (212), `__all__` (216-233)
- Test: `tests/unit/headless_agents/test_report.py` — update the import block (8-19), the
  pins `test_the_run_key_set_is_the_spec_one` (49-57) and
  `test_the_step_key_set_ends_with_attempts` (149-151), append after 263

**Interfaces:**
- Consumes: nothing new (plain `RunResult` values).
- Produces:
  - `RunResult.partial_text: str | None = None`, `RunResult.failure_reason: str | None = None`, `RunResult.usage_complete: bool = True` (last fields; `to_dict()` unchanged)
  - `RUN_KEYS` gains `"budget"` between `"tokens_complete"` and `"steps"`; `STEP_KEYS` ends `..., "attempts", "partial_answer", "steps_after_crossing", "started", "failure_reason", "usage_complete"`
  - `step_entry(..., attempts=(), steps_after_crossing: int | None = None)`
  - `skipped_step_entry(*, index: int, slot: str, role: str, step_dir: str, reason: str) -> dict[str, object]`
  - `budget_line(budget: object) -> str | None`

- [ ] **Step 1: Update the two key pins and write the failing tests.** In
  `test_report.py`, add `budget_line,` after `STEP_KEYS,` and `skipped_step_entry,` after
  `refused_step_entry,` in the import from `headless_agents.report`. In
  `test_the_run_key_set_is_the_spec_one`, the last line of the pinned tuple becomes
  `"tokens_complete", "budget", "steps",`. Replace the PR1 test
  `test_the_step_key_set_ends_with_attempts` entirely with:

```python
def test_the_step_key_set_ends_with_attempts_then_the_budget_keys() -> None:
    assert STEP_KEYS[-6:] == (
        "attempts",
        "partial_answer",
        "steps_after_crossing",
        "started",
        "failure_reason",
        "usage_complete",
    )
    assert "budget" not in STEP_KEYS
```

  and append at the end:

```python
# ── budget (spec 2026-10-13 usage and budget §3.6) ──────────────────────────


def test_a_new_report_has_no_budget() -> None:
    assert _report()["budget"] is None


def test_an_ordinary_step_started_and_carries_no_budget_record() -> None:
    entry = step_entry(index=1, slot="run", role="r", step_dir="d", result=_result())
    assert (entry["partial_answer"], entry["steps_after_crossing"]) == (None, None)
    assert (entry["started"], entry["failure_reason"], entry["usage_complete"]) == (
        True,
        None,
        True,
    )


def test_a_step_stopped_on_the_cap_keeps_its_partial_apart_from_any_answer() -> None:
    stopped = _result(
        exit_code=7, partial_text="half of it", failure_reason="budget_exhausted", text=None
    )
    entry = step_entry(
        index=1, slot="run", role="r", step_dir="d", result=stopped, steps_after_crossing=1
    )
    assert entry["exit_code"] == 7 and entry["partial_answer"] == "half of it"
    assert (entry["failure_reason"], entry["steps_after_crossing"]) == ("budget_exhausted", 1)


def test_a_step_never_started_spent_nothing_and_says_why() -> None:
    entry = skipped_step_entry(
        index=2, slot="judge", role="j", step_dir="steps/02-judge-j", reason="budget_exhausted"
    )
    assert tuple(entry) == STEP_KEYS
    assert (entry["exit_code"], entry["started"], entry["failure_reason"]) == (
        None,
        False,
        "budget_exhausted",
    )
    assert (entry["tokens"], entry["cost_usd"], entry["attempts"]) == (None, None, [])
    report = with_step(
        _report(), step_entry(index=1, slot="review", role="a", step_dir="d", result=_result())
    )
    report = with_step(report, entry)
    assert (report["tokens"], report["tokens_complete"]) == (TEN_TWO, True)
    assert (report["cost_usd"], report["cost_complete"]) == (0.25, True)


def test_a_refused_step_reached_its_start() -> None:
    entry = refused_step_entry(index=1, slot="review", role="a", step_dir="d")
    assert (entry["exit_code"], entry["started"]) == (2, True)


def test_a_lower_bound_spend_keeps_its_figures_and_marks_the_totals_incomplete() -> None:
    entry = step_entry(
        index=1, slot="run", role="r", step_dir="d", result=_result(usage_complete=False)
    )
    report = dict(with_step(_report(), entry), exit_code=7)
    assert (report["tokens"], report["tokens_complete"]) == (TEN_TWO, False)
    assert (report["cost_usd"], report["cost_complete"]) == (0.25, False)
    assert summary_line(report) == (
        "run 20260925T000000-aaaaaaaa: exit 7, cost ≥ 0.25 USD (incomplete), "
        "input ≥ 10, output ≥ 2 (incomplete)"
    )


def test_the_budget_line_names_spend_cap_and_overshoot() -> None:
    budget = {
        "max_cost": 0.25,
        "max_input_tokens": 1000,
        "spent_cost": 0.375,
        "spent_input_tokens": 1500,
        "spent_complete": True,
        "overshoot_cost": 0.125,
        "overshoot_input_tokens": 500,
    }
    assert budget_line(budget) == (
        "budget  cost 0.375 of 0.25 USD (overshoot 0.125 USD), input 1.5k of 1.0k (overshoot 500)"
    )


def test_an_incomplete_budget_reads_at_least_and_its_overshoot_unknown() -> None:
    budget = {
        "max_cost": None,
        "max_input_tokens": 1000,
        "spent_cost": 0.5,
        "spent_input_tokens": 2000,
        "spent_complete": False,
        "overshoot_cost": None,
        "overshoot_input_tokens": None,
    }
    assert budget_line(budget) == "budget  input ≥ 2.0k of 1.0k (incomplete, overshoot unknown)"


def test_an_uncapped_run_has_no_budget_line() -> None:
    assert budget_line(None) is None
    assert budget_line({"max_cost": None, "max_input_tokens": None}) is None
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/unit/headless_agents/test_report.py -q`
Expected: FAIL at collection, `ImportError: cannot import name 'budget_line' from
'headless_agents.report'`.

- [ ] **Step 3: Implement.** In `result.py`, at the end of `RunResult`'s fields (after
  `workspace`):

```python
    #: Spec 2026-10-13 usage and budget §3.6: what a run stopped on its budget cap had
    #: written so far. NEVER an answer: ``text`` stays ``None`` on any nonzero exit, and
    #: no verdict or finding is ever read from it. ``None`` on every other path.
    partial_text: str | None = None
    #: Set by the budget meter only (``budget_exhausted`` or ``budget_unmeasured``);
    #: ``None`` on every path that does not set it.
    failure_reason: str | None = None
    #: ``False`` when ``tokens``/``cost_usd`` are only a lower bound: a model call killed
    #: in flight, or a capped finish that lacked its measure. The run totals then read
    #: incomplete.
    usage_complete: bool = True
```

  and in `to_dict`'s docstring, after its first line, add:

```python

        ``partial_text``, ``failure_reason`` and ``usage_complete`` are ``run.json``'s
        (its step entry), not ``result.json``'s: this key set is unchanged.
```

  In `report.py`, the key tuples become:

```python
RUN_KEYS: Final = (
    "schema", "kind", "run_id", "target", "status", "exit_code", "verdict", "text",
    "repository", "base", "head", "branch", "lineage", "continues", "findings_from",
    "implement_providers", "commits", "failure_reason", "vendor_check", "cleanup", "pid",
    "started_at", "duration_seconds", "cost_usd", "cost_complete", "tokens",
    "tokens_complete", "budget", "steps",
)  # fmt: skip
STEP_KEYS: Final = (
    "index", "slot", "role", "dir", "provider", "model", "model_reported",
    "model_source", "effort", "timeout_seconds", "exit_code", "duration_seconds",
    "tokens", "cost_usd", "tools", "verdict", "attempts", "partial_answer",
    "steps_after_crossing", "started", "failure_reason", "usage_complete",
)  # fmt: skip
```

  `step_entry` gains the keyword `steps_after_crossing: int | None = None` after
  `attempts`, and its dict ends:

```python
        "attempts": [attempt_entry(attempt) for attempt in attempts],
        # Spec 2026-10-13 usage and budget §3.6: kept apart from any answer.
        "partial_answer": result.partial_text,
        "steps_after_crossing": steps_after_crossing,
        "started": True,
        "failure_reason": result.failure_reason,
        "usage_complete": result.usage_complete,
    }
```

  `refused_step_entry` records that the step reached its start (`started=True`), and
  `skipped_step_entry` follows it:

```python
def refused_step_entry(*, index: int, slot: str, role: str, step_dir: str) -> dict[str, object]:
    """A step refused at its start -- its prompt too large once the diff is known (§3.4):
    recorded with code ``2``, nothing measured because nothing ran."""
    entry: dict[str, object] = dict.fromkeys(STEP_KEYS)
    entry.update(
        index=index, slot=slot, role=role, dir=step_dir, exit_code=2, attempts=[], started=True
    )
    return entry


def skipped_step_entry(
    *, index: int, slot: str, role: str, step_dir: str, reason: str
) -> dict[str, object]:
    """A step never started because an earlier one stopped the run on its budget (spec
    2026-10-13 usage §3.6): no exit code, ``started: false``, the reason. It spent
    nothing, so the run totals leave it out."""
    entry: dict[str, object] = dict.fromkeys(STEP_KEYS)
    entry.update(
        index=index,
        slot=slot,
        role=role,
        dir=step_dir,
        attempts=[],
        started=False,
        failure_reason=reason,
    )
    return entry
```

  `_spenders` and `with_step` become:

```python
def _spenders(steps: Sequence[Mapping[str, object]]) -> list[Mapping[str, object]]:
    """Every step and every attempt of a step: what a run total adds up. A step never
    started spent nothing and is left out."""
    spenders: list[Mapping[str, object]] = []
    for step in steps:
        if step.get("started") is False:
            continue
        spenders.append(step)
        attempts = step.get("attempts")
        if isinstance(attempts, list):
            spenders.extend(a for a in attempts if isinstance(a, Mapping))
    return spenders
```

```python
def with_step(report: Mapping[str, object], step: Mapping[str, object]) -> dict[str, object]:
    """A copy of ``report`` with ``step`` appended and the run totals recomputed,
    attempts included (spec 2026-10-13 usage §3.3)."""
    previous = report.get("steps")
    steps = [*(previous if isinstance(previous, list) else []), dict(step)]
    spenders = _spenders(steps)
    costs = [s.get("cost_usd") for s in spenders]
    measured = [c for c in costs if isinstance(c, int | float) and not isinstance(c, bool)]
    # A spend known only as a lower bound (spec 2026-10-13 usage §3.6) is summed, and
    # makes both totals incomplete.
    bounded = any(s.get("usage_complete") is False for s in spenders)
    updated = dict(report)
    updated["steps"] = steps
    updated["cost_usd"] = sum(measured) if measured else None
    updated["cost_complete"] = len(measured) == len(costs) and not bounded
    updated["tokens"] = run_tokens(spenders)
    updated["tokens_complete"] = (
        all(isinstance(s.get("tokens"), Mapping) for s in spenders) and not bounded
    )
    return updated
```

  Before `def write_report` add:

```python
def _float_of(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _int_of(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def budget_line(budget: object) -> str | None:
    """``budget  cost 0.375 of 0.25 USD (overshoot 0.125 USD)``: a capped run's budget as
    ``ha show`` prints it (spec 2026-10-13 usage §3.6); ``None`` for an uncapped run.

    An incomplete spend reads as a lower bound (``≥``) and its overshoot as unknown.
    """
    if not isinstance(budget, Mapping):
        return None
    complete = budget.get("spent_complete") is not False
    at_least = "" if complete else "≥ "
    figures: list[str] = []
    max_cost, spent_cost = _float_of(budget.get("max_cost")), _float_of(budget.get("spent_cost"))
    if max_cost is not None and spent_cost is not None:
        figure = f"cost {at_least}{_usd(spent_cost)} of {_usd(max_cost)} USD"
        overshoot = _float_of(budget.get("overshoot_cost"))
        if complete and overshoot is not None:
            figure += f" (overshoot {_usd(overshoot)} USD)"
        figures.append(figure)
    max_tokens = _int_of(budget.get("max_input_tokens"))
    spent_tokens = _int_of(budget.get("spent_input_tokens"))
    if max_tokens is not None and spent_tokens is not None:
        figure = f"input {at_least}{_count(spent_tokens)} of {_count(max_tokens)}"
        over_tokens = _int_of(budget.get("overshoot_input_tokens"))
        if complete and over_tokens is not None:
            figure += f" (overshoot {_count(over_tokens)})"
        figures.append(figure)
    if not figures:
        return None
    line = "budget  " + ", ".join(figures)
    return line if complete else line + " (incomplete, overshoot unknown)"
```

  and add `"budget_line"` and `"skipped_step_entry"` to `__all__` (sorted).

- [ ] **Step 4: Run the tests** — `uv run pytest tests/unit/headless_agents/test_report.py tests/unit/headless_agents/test_show.py tests/unit/headless_agents/test_cli.py tests/unit/headless_agents/test_engine_execute.py tests/unit/headless_agents/test_review.py -q` → all pass (the `RUN_KEYS` users — `test_show.py:53/143/935`, `test_cli.py:1493` — follow the tuple).

- [ ] **Step 5: Lint and type-check** — `uv run ruff format src tests && uv run ruff check src tests && uv run mypy src` exit 0.

- [ ] **Step 6: Commit**

```sh
git add src/headless_agents/result.py
git add src/headless_agents/report.py
git add tests/unit/headless_agents/test_report.py
git commit -m "✨ feat(report): add the budget record keys, never-started steps and lower-bound totals"
```

---

### Task 5: `budget_exhausted` as a final status, and `ha show` of a budget stop

**Files:**
- Modify: `src/headless_agents/runs.py` — `FINAL_STATUSES` (27-29)
- Modify: `src/headless_agents/show.py` — import (26), `_STATUS_WORDS` (359-362), new
  `_budget_lines` before `_dash` (449), `_step_cells` (453-466), `render` (612-614, after
  the `total` line)
- Test: `tests/unit/headless_agents/test_runs.py` (append after 540),
  `tests/unit/headless_agents/test_lineage.py` (append after 246),
  `tests/unit/headless_agents/test_show.py` (append after 1005)

**Interfaces:**
- Consumes: `budget_line`, the step keys (Task 4).
- Produces: `"budget_exhausted" in FINAL_STATUSES` (hence `STORED_STATUSES`, the registry,
  lineages and `effective_status`); `show._budget_lines(report: Mapping[str, object], steps: Sequence[object]) -> list[str]`.

- [ ] **Step 1: Write the failing tests.** Append at the end of `test_runs.py`:

```python
# ── budget stops (spec 2026-10-13 usage and budget §3.6) ────────────────────


def test_a_budget_stop_is_a_final_status_the_registry_records(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    entry = registry.register(run_dir=None, target=_TARGET, repository=None, lineage=None)
    registry.set_status(entry.run_id, "budget_exhausted")
    again = registry.resolve(entry.run_id)
    assert again.status == "budget_exhausted" and "budget_exhausted" in STORED_STATUSES
    assert registry.effective_status(again, None) == "budget_exhausted"
```

  at the end of `test_lineage.py`:

```python
def test_a_member_stopped_on_its_budget_round_trips(tmp_path: Path) -> None:
    """Spec 2026-10-13 usage and budget §3.6: lineages record ``budget_exhausted``."""
    state = tmp_path / "state"
    stopped = replace(_lineage(tmp_path), members={_OWNER: "budget_exhausted"})
    lineage.create(state, stopped)
    assert lineage.load(state, _OWNER).members == {_OWNER: "budget_exhausted"}
```

  and at the end of `test_show.py`:

```python
# ── budget (spec 2026-10-13 usage and budget §3.6) ──────────────────────────

_BUDGET = {
    "max_cost": None,
    "max_input_tokens": 1000,
    "spent_cost": 0.0042,
    "spent_input_tokens": 1500,
    "spent_complete": True,
    "overshoot_cost": None,
    "overshoot_input_tokens": 500,
}


def _stopped_step(**fields: object) -> dict[str, object]:
    step = dict(
        _STEP,
        provider="opencode",
        model="opencode-go/glm-5.3-flash",
        exit_code=7,
        tokens={"input": 1500, "output": 40, "fresh": 300, "cached": 1200, "thinking": 0},
        cost_usd=0.0042,
        tools={"read": 3},
        attempts=[],
        partial_answer="The first half of the review.\n",
        steps_after_crossing=0,
        started=True,
        failure_reason="budget_exhausted",
        usage_complete=True,
    )
    step.update(fields)
    return step


GOLDEN_BUDGET = """\
20260925T120000-ab12cd34  codex  exit 7  budget exhausted (budget_exhausted)
task    Explain what this repository does.
  run  codex  opencode  opencode-go/glm-5.3-flash  7  1m05s  in 1k out 40  $0.00  read 3
total   in 1k out 40  $0.00
budget  input 1.5k of 1.0k (overshoot 500)
step    run codex: budget_exhausted
--- run codex: partial answer (budget exhausted, not an answer) ---
The first half of the review.
"""


def test_a_run_stopped_on_its_budget_renders_as_the_golden_text(home: Home) -> None:
    report = home.report(
        status="budget_exhausted",
        exit_code=7,
        failure_reason="budget_exhausted",
        text=None,
        steps=[_stopped_step()],
        tokens={"input": 1500, "output": 40, "fresh": 300, "cached": 1200, "thinking": 0},
        tokens_complete=True,
        cost_usd=0.0042,
        cost_complete=True,
        budget=_BUDGET,
    )
    rendered = show.render(_shown(report, task="Explain what this repository does."))
    assert rendered == GOLDEN_BUDGET


def test_an_incomplete_spend_reads_at_least_with_unknown_overshoot(home: Home) -> None:
    budget = dict(_BUDGET, spent_complete=False, overshoot_input_tokens=None)
    report = home.report(
        status="budget_exhausted",
        exit_code=7,
        steps=[_stopped_step(usage_complete=False)],
        tokens={"input": 1500, "output": 40},
        tokens_complete=False,
        budget=budget,
    )
    lines = show.render(_shown(report)).splitlines()
    assert "budget  input ≥ 1.5k of 1.0k (incomplete, overshoot unknown)" in lines
    assert "total   in 1k out 40 (incomplete)  $0.03" in lines


def test_every_partial_is_labelled_and_a_skipped_step_says_why(home: Home) -> None:
    """A review panel: its first member crossed, the second and the judge never started."""
    skipped = dict.fromkeys(_stopped_step())
    skipped.update(slot="review", role="oc-b", index=2, attempts=[], started=False)
    skipped["failure_reason"] = "budget_exhausted"
    judge = dict(skipped, slot="judge", role="oc-judge", index=3)
    report = home.report(
        status="budget_exhausted",
        exit_code=7,
        text=None,
        steps=[_stopped_step(slot="review", role="oc-a"), skipped, judge],
        budget=_BUDGET,
    )
    lines = show.render(_shown(report)).splitlines()
    (judge_row,) = [line for line in lines if line.lstrip().startswith("judge ")]
    assert judge_row.split() == ["judge", "oc-judge", "-", "-", "-", "-", "-", "-", "-"]
    assert "step    review oc-b: not started, budget_exhausted" in lines
    assert "step    judge oc-judge: not started, budget_exhausted" in lines
    assert "--- review oc-a: partial answer (budget exhausted, not an answer) ---" in lines
    assert not any(line.startswith("--- run") for line in lines), "no answer block"


def test_ha_show_derives_a_budget_stop_from_the_registry(home: Home) -> None:
    home.read_only(entry_status="budget_exhausted", status="budget_exhausted", exit_code=7)
    shown = home.rebuild()
    assert shown.report["status"] == "budget_exhausted" and not shown.unknown


def test_ha_show_reads_a_lineage_member_stopped_on_its_budget(home: Home) -> None:
    home.write_run(member="budget_exhausted", status="budget_exhausted", exit_code=7)
    shown = home.rebuild()
    assert shown.report["status"] == "budget_exhausted" and not shown.unknown
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/unit/headless_agents/test_runs.py tests/unit/headless_agents/test_lineage.py tests/unit/headless_agents/test_show.py -q`
Expected: 7 FAIL — the registry and lineage tests with `Unknown: ... status is malformed`
/ `members are malformed`; the golden and line tests on the missing `budget`, `step` and
`partial answer` lines and on `exit 7  budget_exhausted` (no status word); the two
`rebuild` tests with `unknown` set.

- [ ] **Step 3: Implement.** In `runs.py`:

```python
#: ``budget_exhausted``: the run stopped at its budget cap, exit 7 (spec 2026-10-13
#: usage and budget §3.6), in the registry and in a lineage alike.
FINAL_STATUSES: Final = frozenset(
    {"answered", "failed", "committed", "no_change", "approved", "changes", "budget_exhausted"}
)
```

  In `show.py`, the report import becomes `from .report import KIND, PROMPT_FILE, RUN_JSON,
  RUN_KEYS, SCHEMA, budget_line`; `_STATUS_WORDS` gains `"budget_exhausted": "budget
  exhausted",`; `_step_cells` becomes (a never-started step shows no model):

```python
def _step_cells(step: Mapping[str, object]) -> list[str]:
    model, verdict = step.get("model"), step.get("verdict")
    if step.get("started") is False:
        # Never started (spec 2026-10-13 usage §3.6): no model ran, not even "(auto)".
        model = "-"
    return [
        _dash(step.get("slot")),
        _dash(step.get("role")),
        _dash(step.get("provider")),
        model if isinstance(model, str) and model.strip() else "(auto)",
        _dash(step.get("exit_code")),
        format_duration(step.get("duration_seconds")),
        format_tokens(step.get("tokens")),
        format_cost(step.get("cost_usd")),
        format_tools(step.get("tools")),
        verdict.upper() if isinstance(verdict, str) else "",
    ]
```

  before `def _dash` add:

```python
def _budget_lines(report: Mapping[str, object], steps: Sequence[object]) -> list[str]:
    """The budget, each step's budget reason, then each partial answer, labelled so that
    none reads as an answer (spec 2026-10-13 usage and budget §3.6). Nothing for a run
    without a budget record: an older report renders as before."""
    lines: list[str] = []
    budget = budget_line(report.get("budget"))
    if budget is not None:
        lines.append(budget)
    named = [step for step in steps if isinstance(step, dict)]
    for step in named:
        reason = step.get("failure_reason")
        if isinstance(reason, str):
            started = "not started, " if step.get("started") is False else ""
            lines.append(f"step    {step.get('slot')} {step.get('role')}: {started}{reason}")
    for step in named:
        partial = step.get("partial_answer")
        if isinstance(partial, str) and partial.strip():
            lines.append(
                f"--- {step.get('slot')} {step.get('role')}: "
                "partial answer (budget exhausted, not an answer) ---"
            )
            lines.append(partial.rstrip("\n"))
    return lines
```

  and in `render`, right after `lines.append(totals)`:

```python
    totals = _totals_line(report)
    if totals is not None:
        lines.append(totals)
    lines.extend(_budget_lines(report, steps if isinstance(steps, list) else []))
```

- [ ] **Step 4: Run the tests** — `uv run pytest tests/unit/headless_agents/test_runs.py tests/unit/headless_agents/test_lineage.py tests/unit/headless_agents/test_show.py -q` → all pass (the goldens of `test_show.py` are unchanged: an older report has no budget, reason or partial).

- [ ] **Step 5: Lint and type-check** — `uv run ruff format src tests && uv run ruff check src tests && uv run mypy src` exit 0.

- [ ] **Step 6: Commit**

```sh
git add src/headless_agents/runs.py
git add src/headless_agents/show.py
git add tests/unit/headless_agents/test_runs.py
git add tests/unit/headless_agents/test_lineage.py
git add tests/unit/headless_agents/test_show.py
git commit -m "✨ feat(show): record budget_exhausted and show the budget and labelled partials"
```

---

### Task 6: opencode's `StepMeter` — incremental reading, last version wins, missing measures

**Files:**
- Modify: `src/headless_agents/providers/opencode.py` — imports (69-110), `extract_report`
  (576-598, its body moves to a new `report_text`), after `telemetry` (679-711): new
  `_step_key`, `unfinished_step`, `_non_negative`, `_token_count`, `part_cost`,
  `part_input_tokens`, `StepMeter`
- Test: `tests/unit/headless_agents/test_provider_opencode.py` (imports 23, append after 1447)

**Interfaces:**
- Consumes: `Ledger`, `Caps` (Task 1).
- Produces:
  - `report_text(events_log: Path) -> str` (`extract_report` writes it, unchanged behaviour)
  - `unfinished_step(events_log: Path) -> bool`
  - `part_cost(part: Mapping[str, object]) -> float | None`, `part_input_tokens(part: Mapping[str, object]) -> int | None`
  - `class StepMeter(ledger: Ledger)` with `ledger`, `read(events_log: Path, *, final: bool = False) -> None`, properties `unmeasured`, `in_flight`, `must_stop` (all `bool`), `close() -> None`

- [ ] **Step 1: Write the failing tests.** In `test_provider_opencode.py` add `from
  headless_agents.budget import Caps, Ledger` right after `from headless_agents import
  procgroup`; append at the end:

```python
# ── budget meter (spec 2026-10-13 usage and budget §3.6) ────────────────────

_NO_COST = object()


def _finish(
    part_id: str,
    *,
    input: int = 100,
    cache_read: int = 0,
    cost: object = 0.25,
    tokens: bool = True,
) -> dict[str, object]:
    """A ``step_finish`` event as opencode writes it, with its part id."""
    part: dict[str, object] = {"id": part_id, "type": "step-finish", "reason": "tool-calls"}
    if tokens:
        part["tokens"] = {
            "total": input + cache_read + 10,
            "input": input,
            "output": 10,
            "reasoning": 0,
            "cache": {"read": cache_read, "write": 0},
        }
    if cost is not _NO_COST:
        part["cost"] = cost
    return {"type": "step_finish", "part": part}


def _start(part_id: str) -> dict[str, object]:
    return {"type": "step_start", "part": {"id": part_id, "type": "step-start"}}


def _append(path: Path, data: str | bytes) -> None:
    with path.open("ab") as stream:
        stream.write(data.encode("utf-8") if isinstance(data, str) else data)


class TestStepMeter:
    def test_a_part_re_emitted_across_reads_is_counted_once(self, tmp_path: Path) -> None:
        log = tmp_path / "events.jsonl"
        ledger = Ledger(Caps(max_cost=1.0))
        meter = opencode.StepMeter(ledger)
        _append(log, _events(_start("s1"), _finish("f1", cost=0.25)))
        meter.read(log)
        assert ledger.spent_cost == 0.25
        _append(log, _events(_finish("f1", cost=0.5)))
        meter.read(log)
        assert ledger.spent_cost == 0.5, "the last version wins; never 0.75"

    def test_a_line_split_across_two_reads_waits_for_its_newline(self, tmp_path: Path) -> None:
        log = tmp_path / "events.jsonl"
        ledger = Ledger(Caps(max_input_tokens=1000))
        meter = opencode.StepMeter(ledger)
        line = _events(_finish("f1", input=600, cache_read=600)).encode()
        _append(log, line[:40])
        meter.read(log)
        assert ledger.spent_input_tokens == 0 and not meter.must_stop
        _append(log, line[40:])
        meter.read(log)
        assert ledger.spent_input_tokens == 1200, "cache reads count as input (D5)"
        assert ledger.crossed and meter.must_stop

    def test_two_finishes_in_one_read_both_count_and_one_follows_the_crossing(
        self, tmp_path: Path
    ) -> None:
        log = tmp_path / "events.jsonl"
        ledger = Ledger(Caps(max_cost=0.5))
        meter = opencode.StepMeter(ledger)
        _append(log, _events(_finish("f1", cost=0.5), _finish("f2", cost=0.25)))
        meter.read(log)
        assert ledger.spent_cost == 0.75
        assert ledger.steps_after_crossing == 1

    def test_only_the_final_read_takes_an_unterminated_last_line(self, tmp_path: Path) -> None:
        log = tmp_path / "events.jsonl"
        ledger = Ledger(Caps(max_cost=0.5))
        meter = opencode.StepMeter(ledger)
        _append(log, json.dumps(_finish("f1", cost=0.5)))
        meter.read(log)
        assert not ledger.crossed
        meter.read(log, final=True)
        assert ledger.crossed and ledger.steps_after_crossing == 0

    @pytest.mark.parametrize(
        ("caps", "finish"),
        [
            (Caps(max_cost=1.0), _finish("f1", cost=_NO_COST)),
            (Caps(max_cost=1.0), _finish("f1", cost="0.1")),
            (Caps(max_cost=1.0), _finish("f1", cost=-0.1)),
            (Caps(max_cost=1.0), _finish("f1", cost=float("nan"))),
            (Caps(max_cost=1.0), _finish("f1", cost=True)),
            (Caps(max_input_tokens=1000), _finish("f1", tokens=False)),
            (
                Caps(max_input_tokens=1000),
                {"type": "step_finish", "part": {"id": "f1", "tokens": {"input": "many"}}},
            ),
            (
                Caps(max_input_tokens=1000),
                {"type": "step_finish", "part": {"id": "f1", "tokens": {"input": 10}}},
            ),
        ],
    )
    def test_absent_malformed_or_partial_usage_under_its_cap_is_unmeasured(
        self, tmp_path: Path, caps: Caps, finish: dict[str, object]
    ) -> None:
        log = tmp_path / "events.jsonl"
        meter = opencode.StepMeter(Ledger(caps))
        _append(log, _events(_start("s1"), finish))
        meter.read(log)
        assert meter.unmeasured and meter.must_stop

    def test_a_measure_no_cap_needs_may_be_absent(self, tmp_path: Path) -> None:
        log = tmp_path / "events.jsonl"
        meter = opencode.StepMeter(Ledger(Caps(max_input_tokens=1000)))
        _append(log, _events(_finish("f1", cost=_NO_COST)))
        meter.read(log)
        assert not meter.unmeasured and meter.ledger.spent_cost is None

    def test_a_measured_zero_is_a_measure(self, tmp_path: Path) -> None:
        log = tmp_path / "events.jsonl"
        ledger = Ledger(Caps(max_cost=1.0, max_input_tokens=1000))
        meter = opencode.StepMeter(ledger)
        _append(log, _events(_finish("f1", input=0, cache_read=0, cost=0)))
        meter.read(log)
        assert not meter.unmeasured and not meter.must_stop
        assert (ledger.spent_cost, ledger.spent_input_tokens) == (0.0, 0)

    def test_a_valid_finish_then_one_missing_its_measure_keeps_a_lower_bound(
        self, tmp_path: Path
    ) -> None:
        log = tmp_path / "events.jsonl"
        ledger = Ledger(Caps(max_cost=1.0))
        meter = opencode.StepMeter(ledger)
        _append(log, _events(_finish("f1", cost=0.25), _finish("f2", cost=_NO_COST)))
        meter.read(log, final=True)
        meter.close()
        assert meter.unmeasured and ledger.complete is False
        assert ledger.record()["spent_cost"] == 0.25
        assert ledger.record()["overshoot_cost"] is None

    def test_a_start_without_its_finish_is_in_flight_and_leaves_a_lower_bound(
        self, tmp_path: Path
    ) -> None:
        log = tmp_path / "events.jsonl"
        ledger = Ledger(Caps(max_cost=0.5))
        meter = opencode.StepMeter(ledger)
        _append(log, _events(_start("s1"), _finish("f1", cost=0.5), _start("s2")))
        meter.read(log, final=True)
        assert meter.in_flight and not meter.unmeasured and ledger.crossed
        meter.close()
        assert ledger.record()["spent_complete"] is False

    def test_close_settles_so_the_next_step_starts_from_what_is_left(self, tmp_path: Path) -> None:
        ledger = Ledger(Caps(max_cost=1.0))
        first, second = tmp_path / "first.jsonl", tmp_path / "second.jsonl"
        _append(first, _events(_start("s1"), _finish("f1", cost=0.5)))
        _append(second, _events(_start("s1"), _finish("f1", cost=0.25)))
        meter = opencode.StepMeter(ledger)
        meter.read(first, final=True)
        meter.close()
        assert ledger.record()["spent_complete"] is True
        meter = opencode.StepMeter(ledger)
        meter.read(second, final=True)
        meter.close()
        assert ledger.spent_cost == 0.75 and not ledger.crossed

    def test_an_absent_stream_reads_as_nothing_new(self, tmp_path: Path) -> None:
        meter = opencode.StepMeter(Ledger(Caps(max_cost=1.0)))
        meter.read(tmp_path / "absent.jsonl", final=True)
        assert not meter.must_stop and not meter.in_flight


class TestUnfinishedStep:
    def test_a_start_no_finish_matched_is_unfinished(self, tmp_path: Path) -> None:
        log = tmp_path / "events.jsonl"
        log.write_text(_events(_start("s1"), _finish("f1"), _start("s2")), encoding="utf-8")
        assert opencode.unfinished_step(log) is True

    def test_matched_starts_and_re_emitted_parts_are_finished(self, tmp_path: Path) -> None:
        log = tmp_path / "events.jsonl"
        log.write_text(
            _events(_start("s1"), _start("s1"), _finish("f1"), _finish("f1")), encoding="utf-8"
        )
        assert opencode.unfinished_step(log) is False
        assert opencode.unfinished_step(tmp_path / "absent.jsonl") is False


def test_report_text_is_what_extract_report_writes(tmp_path: Path) -> None:
    log = tmp_path / "events.jsonl"
    log.write_text(_events(_text("part one", "p1"), _text("part two", "p2")), encoding="utf-8")
    assert opencode.report_text(log) == "part one\n\npart two"
    report = tmp_path / "report.log"
    opencode.extract_report(log, report)
    assert report.read_text(encoding="utf-8") == opencode.report_text(log)
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/unit/headless_agents/test_provider_opencode.py -q`
Expected: 21 FAIL with `AttributeError: module 'headless_agents.providers.opencode' has no
attribute 'StepMeter'` (`'unfinished_step'`, `'report_text'`); every existing test passes.

- [ ] **Step 3: Implement.** Add `import math` after `import json`, and `from ..budget
  import Ledger` before `from ..capability import (`. `extract_report` keeps its docstring
  and its body becomes a call to the new `report_text`, which takes the loop it had:

```python
def extract_report(events_log: Path, report_log: Path) -> None:
    """Rebuild the run's report from the ``text`` parts, in order.

    A part may be emitted more than once as it grows: the LAST version of
    each part id wins, at the position of its first appearance. Every part
    is kept, not just the final one, so a machine-readable trailer the agent
    printed in a message of its own survives. A fence around the whole
    report is removed (see :func:`_unwrap_fence`).
    """
    report = report_text(events_log)
    if report.strip():
        report_log.write_text(report, encoding="utf-8")


def report_text(events_log: Path) -> str:
    """The text :func:`extract_report` writes, without writing it: also what a run stopped
    on its budget cap had said so far (spec 2026-10-13 usage and budget §3.6)."""
    texts: dict[str, str] = {}
    for index, event in enumerate(_events(events_log)):
        if event.get("type") != "text":
            continue
        part = _part(event)
        text = part.get("text")
        if not isinstance(text, str):
            continue
        part_id = part.get("id")
        key = part_id if isinstance(part_id, str) else f"#{index}"
        texts[key] = text
    return _unwrap_fence("\n\n".join(text for text in texts.values() if text.strip()))
```

  After `telemetry` (before `def _writable_workspace_may_have_written`) add:

```python
def _step_key(event: Mapping[str, object], position: int) -> str:
    """A part's id, or its position in the stream when it has none."""
    part_id = _part(event).get("id")
    return part_id if isinstance(part_id, str) else f"#{position}"


def unfinished_step(events_log: Path) -> bool:
    """A ``step_start`` that no ``step_finish`` matched: a model call killed in flight,
    whose consumption is unknown (spec 2026-10-13 usage and budget §3.6). Parts count
    once each, whatever their re-emissions."""
    starts: set[str] = set()
    finishes: set[str] = set()
    for position, event in enumerate(_events(events_log)):
        kind = event.get("type")
        if kind == "step_start":
            starts.add(_step_key(event, position))
        elif kind == "step_finish":
            finishes.add(_step_key(event, position))
    return len(starts) > len(finishes)


def _non_negative(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else None


def _token_count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def part_cost(part: Mapping[str, object]) -> float | None:
    """A ``step_finish`` part's cost in USD; ``None`` when absent or malformed. A
    measured zero is a measure."""
    return _non_negative(part.get("cost"))


def part_input_tokens(part: Mapping[str, object]) -> int | None:
    """A ``step_finish`` part's total input, cache reads included (the figure D5's
    ``--max-input-tokens`` caps); ``None`` when the ``tokens`` block, its ``input`` or
    its ``cache.read`` is absent or malformed."""
    tokens = part.get("tokens")
    if not isinstance(tokens, dict):
        return None
    cache = tokens.get("cache")
    fresh = _token_count(tokens.get("input"))
    read = _token_count(cache.get("read")) if isinstance(cache, dict) else None
    if fresh is None or read is None:
        return None
    return fresh + read


class StepMeter:
    """One capped opencode step, read incrementally into the run's :class:`Ledger`
    (spec 2026-10-13 usage and budget §3.6).

    Each :meth:`read` takes what the stream gained since the last one. An incomplete
    trailing line waits for its newline (the final read takes it as it is). A
    ``step_finish`` part re-emitted across reads REPLACES its earlier version, the
    last-version-wins rule of :func:`step_finish_parts` made incremental, and the
    ledger gets the step's new totals.
    """

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger
        self._offset = 0
        self._pending = b""
        self._position = 0
        self._starts: set[str] = set()
        self._finishes: dict[str, Mapping[str, object]] = {}

    def read(self, events_log: Path, *, final: bool = False) -> None:
        """Take the bytes ``events_log`` gained since the last read. Never raises: an
        unreadable stream reads as nothing new."""
        try:
            with events_log.open("rb") as stream:
                stream.seek(self._offset)
                chunk = stream.read()
        except OSError:
            chunk = b""
        self._offset += len(chunk)
        lines = (self._pending + chunk).split(b"\n")
        self._pending = lines.pop()
        if final and self._pending:
            lines.append(self._pending)
            self._pending = b""
        for line in lines:
            self._take(line)

    def _take(self, raw: bytes) -> None:
        position = self._position
        self._position += 1
        try:
            event = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return
        if not isinstance(event, dict):
            return
        kind = event.get("type")
        if kind == "step_start":
            self._starts.add(_step_key(event, position))
        elif kind == "step_finish":
            key = _step_key(event, position)
            if key not in self._finishes and self.ledger.steps_after_crossing is not None:
                self.ledger.steps_after_crossing += 1
            self._finishes[key] = _part(event)
            self._charge()

    def _charge(self) -> None:
        cost: float | None = None
        tokens: int | None = None
        for part in self._finishes.values():
            spent = part_cost(part)
            if spent is not None:
                cost = spent if cost is None else cost + spent
            given = part_input_tokens(part)
            if given is not None:
                tokens = given if tokens is None else tokens + given
        self.ledger.measure(cost=cost, input_tokens=tokens)

    @property
    def unmeasured(self) -> bool:
        """A finished call lacks the measure a cap needs: the allowance is unknowable."""
        caps = self.ledger.caps
        return any(
            (caps.max_cost is not None and part_cost(part) is None)
            or (caps.max_input_tokens is not None and part_input_tokens(part) is None)
            for part in self._finishes.values()
        )

    @property
    def in_flight(self) -> bool:
        """A call started and not finished: its consumption is unknown."""
        return len(self._starts) > len(self._finishes)

    @property
    def must_stop(self) -> bool:
        return self.ledger.crossed or self.unmeasured

    def close(self) -> None:
        """After the final read: a lower-bound spend makes the ledger incomplete, then the
        step is settled so that the next one starts from it."""
        if self.unmeasured or self.in_flight:
            self.ledger.complete = False
        self.ledger.settle()
```

- [ ] **Step 4: Run the tests** — `uv run pytest tests/unit/headless_agents/test_provider_opencode.py tests/unit/headless_agents/test_tool_counts.py -q` → all pass.

- [ ] **Step 5: Lint and type-check** — `uv run ruff format src tests && uv run ruff check src tests && uv run mypy src` exit 0.

- [ ] **Step 6: Commit**

```sh
git add src/headless_agents/providers/opencode.py
git add tests/unit/headless_agents/test_provider_opencode.py
git commit -m "✨ feat(opencode): meter a step's usage incrementally into the run ledger"
```

---

### Task 7: opencode enforces the cap — 0.1 s polls, immediate kill, exit 7 or 1, the partial

**Files:**
- Modify: `src/headless_agents/spec.py` — module docstring (37), imports (48), `RunSpec`
  fields (102)
- Modify: `src/headless_agents/providers/opencode.py` — the `..budget`/`..capability`
  imports, after `QUOTA_POLL_SECONDS` (831), `run_opencode` (901-1097: signature and
  docstring 901-915, the poll loop 1016-1052, after the `with` block 1053), `OpenCodeProvider.run`
  (1205-1290: the `run_opencode(...)` call 1229-1246 and the `record(...)` after it)
- Test: `tests/unit/headless_agents/test_provider_opencode.py` — imports (17-28), the
  shared helper `_install` (578-601), append at the end

**Interfaces:**
- Consumes: `StepMeter`, `report_text`, `unfinished_step` (Task 6); `Ledger`,
  `BUDGET_EXHAUSTED`, `BUDGET_UNMEASURED` (Task 1); `BUDGET_EXIT_CODE`,
  `kill_process_group` (Task 1); the `RunResult` fields (Task 4).
- Produces:
  - `RunSpec.budget: Ledger | None = None` (last field)
  - `opencode.BUDGET_POLL_SECONDS: Final = 0.1`
  - `run_opencode(..., meter: StepMeter | None = None) -> int` (returns `BUDGET_EXIT_CODE` or `1` on a budget stop)
  - `OpenCodeProvider.run` fills `RunResult.partial_text`, `failure_reason`, `usage_complete`

- [ ] **Step 1: Write the failing tests.** In `test_provider_opencode.py` add `from
  collections.abc import Callable` after `import time`, and `BUDGET_EXIT_CODE,` as the first
  name of the `from headless_agents.capability import (...)` list. In the shared helper
  `_install`, right after its `terminate_process_group` patch, add the same stub for the
  immediate kill (no `_FakeProcess` may ever reach a real `os.killpg` on its fake pid):

```python
    monkeypatch.setattr(opencode, "terminate_process_group", lambda process: process.kill())
    monkeypatch.setattr(opencode, "kill_process_group", lambda process: process.kill())
```

  Append at the end:

```python
# ── budget enforcement (spec 2026-10-13 usage and budget §3.6, A2) ──────────


def _paced_opencode_script(
    tmp_path: Path,
    *chunks: tuple[str, float],
    then: str = "sleep 30",
    ignore_sigterm: bool = False,
    pid_file: Path | None = None,
    name: str = "opencode",
) -> Path:
    """A REAL child writing its stream over time: each chunk is printed, then the script
    sleeps for its delay; ``then`` ends it (``exit 0``, or a long ``sleep`` that only a
    kill ends). ``ignore_sigterm`` makes a graceful termination wait its whole grace."""
    script = tmp_path / name
    lines = ["#!/usr/bin/env bash"]
    if ignore_sigterm:
        lines.append("trap '' TERM")
    if pid_file is not None:
        lines.append(f"echo $$ > {shlex.quote(str(pid_file))}")
    for text, delay in chunks:
        lines.append(f"printf '%s' {shlex.quote(text)}")
        if delay:
            lines.append(f"sleep {delay}")
    lines.append(then)
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def _capped_run(tmp_path: Path, script: Path, caps: Caps) -> tuple[int, Ledger, float]:
    ledger = Ledger(caps)
    started = time.monotonic()
    code = _run(
        tmp_path,
        executable=str(script),
        profile=_profile(mcp=None),
        meter=opencode.StepMeter(ledger),
        timeout_seconds=20.0,
    )
    return code, ledger, time.monotonic() - started


class TestBudgetEnforcement:
    def test_a_crossing_kills_the_group_at_once_and_exits_7(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "pid"
        script = _paced_opencode_script(
            tmp_path,
            (_events(_start("s1"), _text("so far"), _finish("f1", cost=0.5)), 0),
            ignore_sigterm=True,
            pid_file=pid_file,
        )
        code, ledger, elapsed = _capped_run(tmp_path, script, Caps(max_cost=0.5))
        assert code == BUDGET_EXIT_CODE
        assert elapsed < 3.0, f"took {elapsed:.1f}s: the kill waited for a grace"
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid_file.read_text().strip()), 0)
        assert ledger.record()["spent_cost"] == 0.5 and ledger.steps_after_crossing == 0
        stderr = (tmp_path / "out" / "stderr.log").read_text(encoding="utf-8")
        assert "budget (max_cost 0.5 USD) is exhausted" in stderr

    def test_a_part_re_emitted_across_polls_is_counted_once(self, tmp_path: Path) -> None:
        script = _paced_opencode_script(
            tmp_path,
            (_events(_start("s1"), _finish("f1", cost=0.25)), 0.4),
            (_events(_finish("f1", cost=0.5), _text("REPORT")), 0),
            then="exit 0",
        )
        code, ledger, _ = _capped_run(tmp_path, script, Caps(max_cost=0.75))
        assert code == 0, "0.25 then 0.5 of one part is 0.5, under the cap"
        assert ledger.record()["spent_cost"] == 0.5

    def test_a_line_split_across_two_polls_is_read_whole(self, tmp_path: Path) -> None:
        line = _events(_finish("f1", input=600, cache_read=600))
        script = _paced_opencode_script(
            tmp_path, (_events(_start("s1")) + line[:50], 0.4), (line[50:], 0)
        )
        code, ledger, elapsed = _capped_run(tmp_path, script, Caps(max_input_tokens=1000))
        assert code == BUDGET_EXIT_CODE and elapsed < 3.0
        assert ledger.record()["spent_input_tokens"] == 1200

    def test_two_steps_finishing_inside_one_poll_both_count(self, tmp_path: Path) -> None:
        script = _paced_opencode_script(
            tmp_path,
            (
                _events(
                    _start("s1"),
                    _finish("f1", cost=0.5),
                    _start("s2"),
                    _finish("f2", cost=0.25),
                ),
                0,
            ),
        )
        code, ledger, _ = _capped_run(tmp_path, script, Caps(max_cost=0.5))
        assert code == BUDGET_EXIT_CODE
        assert ledger.record() == {
            "max_cost": 0.5,
            "max_input_tokens": None,
            "spent_cost": 0.75,
            "spent_input_tokens": 200,
            "spent_complete": True,
            "overshoot_cost": 0.25,
            "overshoot_input_tokens": None,
        }
        assert ledger.steps_after_crossing == 1

    def test_an_own_exit_and_a_kill_at_the_same_spend_give_the_same_record(
        self, tmp_path: Path
    ) -> None:
        stream = _events(_start("s1"), _text("so far"), _finish("f1", input=900, cache_read=300))
        records = []
        for then in ("exit 0", "sleep 30"):
            work = tmp_path / then.replace(" ", "-")
            work.mkdir()
            script = _paced_opencode_script(work, (stream, 0), then=then)
            code, ledger, _ = _capped_run(work, script, Caps(max_input_tokens=1000))
            assert code == BUDGET_EXIT_CODE, then
            records.append((ledger.record(), ledger.steps_after_crossing))
        assert records[0] == records[1]
        assert records[0][0]["overshoot_input_tokens"] == 200

    def test_a_crossing_found_only_by_the_final_read_exits_7(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The fake writes its whole stream and exits inside its first wait: no poll ran.
        _install(
            monkeypatch,
            _FakeProcess(returncode=0, events=_events(_text("REPORT"), _finish("f1", cost=0.5))),
        )
        monkeypatch.setattr(
            opencode, "kill_process_group", lambda process: pytest.fail("nothing to kill")
        )
        ledger = Ledger(Caps(max_cost=0.5))
        code = _run(tmp_path, profile=_profile(mcp=None), meter=opencode.StepMeter(ledger))
        assert code == BUDGET_EXIT_CODE and ledger.crossed

    def test_a_capped_run_polls_every_tenth_of_a_second(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        timeouts: list[float | None] = []

        class _Recording(_FakeProcess):
            def wait(self, timeout: float | None = None) -> int:
                timeouts.append(timeout)
                return super().wait(timeout)

        for meter in (None, opencode.StepMeter(Ledger(Caps(max_cost=1.0)))):
            work = tmp_path / ("capped" if meter else "uncapped")
            work.mkdir()
            _install(monkeypatch, _Recording(returncode=0, events=GOOD_EVENTS))
            _run(work, meter=meter)
        assert timeouts == [opencode.QUOTA_POLL_SECONDS, opencode.BUDGET_POLL_SECONDS]
        assert opencode.BUDGET_POLL_SECONDS == 0.1

    def test_a_natural_failure_under_the_caps_keeps_its_classification(
        self, tmp_path: Path
    ) -> None:
        stream = _events(_start("s1"), _finish("f1", cost=0.25))  # no text: no report
        codes = []
        for meter in (None, opencode.StepMeter(Ledger(Caps(max_cost=1.0)))):
            work = tmp_path / ("capped" if meter else "uncapped")
            work.mkdir()
            script = _paced_opencode_script(work, (stream, 0), then="exit 0")
            codes.append(
                _run(work, executable=str(script), profile=_profile(mcp=None), meter=meter)
            )
        assert codes == [PROVIDER_FALLBACK_EXIT_CODE, PROVIDER_FALLBACK_EXIT_CODE]

    def test_a_finish_without_its_measure_kills_at_once_and_exits_1(self, tmp_path: Path) -> None:
        script = _paced_opencode_script(
            tmp_path,
            (_events(_start("s1"), _finish("f1", cost=_NO_COST)), 0),
            ignore_sigterm=True,
        )
        code, ledger, elapsed = _capped_run(tmp_path, script, Caps(max_cost=1.0))
        assert code == 1 and elapsed < 3.0
        assert ledger.record()["spent_complete"] is False
        stderr = (tmp_path / "out" / "stderr.log").read_text(encoding="utf-8")
        assert "reported no usage for the run's budget (max_cost 1 USD)" in stderr

    def test_a_measured_zero_is_no_reason_to_stop(self, tmp_path: Path) -> None:
        script = _paced_opencode_script(
            tmp_path,
            (_events(_start("s1"), _finish("f1", input=0, cost=0), _text("REPORT")), 0),
            then="exit 0",
        )
        code, ledger, _ = _capped_run(tmp_path, script, Caps(max_cost=1.0, max_input_tokens=10))
        assert code == 0 and ledger.record()["spent_complete"] is True

    def test_a_crossing_then_a_call_killed_in_flight_exits_7_with_a_lower_bound(
        self, tmp_path: Path
    ) -> None:
        script = _paced_opencode_script(
            tmp_path, (_events(_start("s1"), _finish("f1", cost=0.5), _start("s2")), 0)
        )
        code, ledger, _ = _capped_run(tmp_path, script, Caps(max_cost=0.5))
        assert code == BUDGET_EXIT_CODE
        record = ledger.record()
        assert (record["spent_cost"], record["spent_complete"], record["overshoot_cost"]) == (
            0.5,
            False,
            None,
        )

    def test_the_same_inputs_uncapped_run_as_today(self, tmp_path: Path) -> None:
        script = _paced_opencode_script(
            tmp_path,
            (_events(_start("s1"), _text("REPORT"), _finish("f1", cost=50.0)), 0),
            then="exit 0",
        )
        code = _run(tmp_path, executable=str(script), profile=_profile(mcp=None))
        assert code == 0


def _metered_fake(events: str, code: int) -> tuple[Callable[..., int], list[dict[str, object]]]:
    """A ``run_opencode`` stand-in that writes ``events`` and meters them as the real one
    does after its child exits."""
    calls: list[dict[str, object]] = []

    def fake_run(**kwargs: object) -> int:
        calls.append(kwargs)
        events_log = kwargs["events_log"]
        assert isinstance(events_log, Path)
        events_log.parent.mkdir(parents=True, exist_ok=True)
        events_log.write_text(events, encoding="utf-8")
        meter = kwargs["meter"]
        if isinstance(meter, opencode.StepMeter):
            meter.read(events_log, final=True)
            meter.close()
        return code

    return fake_run, calls


class TestBudgetResult:
    def _spec(self, tmp_path: Path, ledger: Ledger | None) -> RunSpec:
        return RunSpec(
            prompt="P",
            model="opencode-go/m",
            profile=_profile(mcp=None),
            run_dir=tmp_path / "runs" / "r1",
            budget=ledger,
        )

    def test_the_runs_ledger_meters_the_step(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_run, calls = _metered_fake(GOOD_EVENTS, 0)
        monkeypatch.setattr(opencode, "run_opencode", fake_run)
        ledger = Ledger(Caps(max_cost=1.0))
        opencode.OpenCodeProvider().run(self._spec(tmp_path, ledger))
        meter = calls[0]["meter"]
        assert isinstance(meter, opencode.StepMeter) and meter.ledger is ledger
        opencode.OpenCodeProvider().run(self._spec(tmp_path / "uncapped", None))
        assert calls[1]["meter"] is None

    def test_a_budget_stop_keeps_its_partial_apart_from_any_answer(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        events = _events(_start("s1"), _text("half of it"), _finish("f1", cost=0.5))
        fake_run, _ = _metered_fake(events, BUDGET_EXIT_CODE)
        monkeypatch.setattr(opencode, "run_opencode", fake_run)
        result = opencode.OpenCodeProvider().run(self._spec(tmp_path, Ledger(Caps(max_cost=0.5))))
        assert (result.exit_code, result.text, result.partial_text) == (
            BUDGET_EXIT_CODE,
            None,
            "half of it",
        )
        assert (result.failure_reason, result.usage_complete) == ("budget_exhausted", True)
        assert result.cost_usd == 0.5
        written = json.loads((tmp_path / "runs" / "r1" / "result.json").read_text())
        assert written == result.to_dict() and "partial_text" not in written

    def test_an_unmeasured_stop_says_why_and_its_usage_is_a_lower_bound(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        events = _events(_start("s1"), _finish("f1", cost=0.25), _finish("f2", cost=_NO_COST))
        fake_run, _ = _metered_fake(events, 1)
        monkeypatch.setattr(opencode, "run_opencode", fake_run)
        result = opencode.OpenCodeProvider().run(self._spec(tmp_path, Ledger(Caps(max_cost=1.0))))
        assert (result.exit_code, result.failure_reason) == (1, "budget_unmeasured")
        assert result.partial_text is None and result.usage_complete is False

    def test_a_call_killed_in_flight_uncapped_is_a_lower_bound_and_nothing_else(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        events = _events(_start("s1"), _finish("f1"), _start("s2"))
        fake_run, _ = _metered_fake(events, TIMEOUT_EXIT_CODE)
        monkeypatch.setattr(opencode, "run_opencode", fake_run)
        result = opencode.OpenCodeProvider().run(self._spec(tmp_path, None))
        assert (result.exit_code, result.failure_reason, result.partial_text) == (
            TIMEOUT_EXIT_CODE,
            None,
            None,
        )
        assert result.usage_complete is False and result.tokens is not None

    def test_a_tripwire_outranks_a_budget_stop(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        events = _events(_start("s1"), _text("half"), _finish("f1", cost=0.5))
        fake_run, _ = _metered_fake(events, BUDGET_EXIT_CODE)
        monkeypatch.setattr(opencode, "run_opencode", fake_run)
        monkeypatch.setattr(opencode, "settle_run", lambda tripwire, code, log: (1, (".git/HEAD",)))
        result = opencode.OpenCodeProvider().run(self._spec(tmp_path, Ledger(Caps(max_cost=0.5))))
        assert (result.exit_code, result.failure_reason, result.partial_text) == (1, None, None)
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/unit/headless_agents/test_provider_opencode.py -q`
Expected: FAIL — every test using `_install` errors with `AttributeError: <module
'headless_agents.providers.opencode'> has no attribute 'kill_process_group'` (the helper's
new stub); the new real-child tests with `TypeError: run_opencode() got an unexpected
keyword argument 'meter'`; the `TestBudgetResult` tests with `TypeError: RunSpec.__init__()
got an unexpected keyword argument 'budget'`. `test_the_same_inputs_uncapped_run_as_today` is
the guard of spec §4 "the same inputs uncapped behave as today": it passes before and after.

- [ ] **Step 3: Implement.** In `spec.py`, add `from .budget import Ledger` before `from
  .context import ContextBundle`, the field `budget: Ledger | None = None` after
  `output_schema`, and at the end of the module docstring:

```python
``budget`` is the run's :class:`~headless_agents.budget.Ledger` when the run is capped
(spec 2026-10-13 usage and budget §3.6), ``None`` otherwise. Only a rail that reports
usage during a run (opencode) can enforce it; ``ha run`` refuses a cap on any other rail
before anything starts, and a library caller that hands one to another rail gets an
uncapped run.
```

  In `opencode.py` the two imports become:

```python
from ..budget import BUDGET_EXHAUSTED, BUDGET_UNMEASURED, Ledger
from ..capability import (
    AGENT_MARKER_VARIABLE,
    BUDGET_EXIT_CODE,
    INVALID_USAGE_EXIT_CODE,
    PROVIDER_FALLBACK_EXIT_CODE,
    TIMEOUT_EXIT_CODE,
    TIMEOUT_REPLAYABLE_EXIT_CODE,
    failure_code_after_a_write,
    kill_process_group,
    terminate_process_group,
)
```

  After `QUOTA_POLL_SECONDS: Final = 0.5`:

```python
#: How often a CAPPED run polls (spec 2026-10-13 usage and budget §3.6, A2): a crossing
#: is noticed within this interval of the ``step_finish`` that made it, then killed at once.
BUDGET_POLL_SECONDS: Final = 0.1
```

  `run_opencode` gains the keyword `meter: StepMeter | None = None` after `workspace`, and
  this paragraph at the end of its docstring:

```python
    ``meter`` caps the run (spec 2026-10-13 usage and budget §3.6): the stream is read at
    every :data:`BUDGET_POLL_SECONDS` poll, and once the run's spend reaches a cap, or a
    finished call lacks the measure a cap needs, the process group is killed at once
    (no grace). After a final read, a spend at or over a cap returns ``7`` whoever ended
    the child, an unmeasured finish ``1``; a spend under every cap is classified as
    without a meter.

    ``environment`` is the AMBIENT environment to read ``PATH``/``LANG``, the
    bearer variable and the profile's passthrough variables from; the child
    gets a rebuilt one whose ``HOME`` is the ephemeral directory. When the
    profile declares a server, the bearer must be visible -- as the profile's
    literal value or under the named variable -- or the run refuses to start,
    so a scoped bearer can never be silently replaced by an ambient one.

    ``workspace`` moves ``--dir`` and the child's cwd to its path (the HOME
    stays the ephemeral run directory) and widens the inline config's
    allowlist; in a WRITABLE workspace it also taints the deadline and
    failure codes the way an MCP write does, through :func:`write_tool_started`.
```

  In its poll loop, `poll_seconds` replaces `QUOTA_POLL_SECONDS`, and a capped poll reads
  the stream before the quota scan:

```python
            timed_out = False
            quota_hit = False
            poll_seconds = BUDGET_POLL_SECONDS if meter is not None else QUOTA_POLL_SECONDS
            try:
                started = time.monotonic()
                quota_offset = 0
                while True:
                    left = remaining - (time.monotonic() - started)
                    if left <= 0:
                        terminate_process_group(process)
                        # A timeout proves nothing BY ITSELF: the run may have
                        # written and then hung. The stream decides, after the
                        # kill, whether a call could even have started -- see
                        # _deadline_exit_code.
                        timed_out = True
                        break
                    try:
                        process.wait(timeout=min(poll_seconds, left))
                    except subprocess.TimeoutExpired:
                        if meter is not None:
                            meter.read(events_log)
                            if meter.must_stop:
                                # A budget stop has no answer to wait for: no grace.
                                kill_process_group(process)
                                break
                        exhausted, quota_offset = _quota_exhausted(stderr_log, quota_offset)
```

  Right after the `with (events_log.open(...) ..., stderr_log.open(...) ...)` block and
  before `if quota_hit:`:

```python
        if meter is not None:
            # The outcome depends on the measured spend only, never on whether ha's kill
            # or the child's own exit came first (spec 2026-10-13 usage §3.6).
            meter.read(events_log, final=True)
            meter.close()
            if meter.ledger.crossed:
                with stderr_log.open("a", encoding="utf-8") as stderr_stream:
                    stderr_stream.write(
                        f"opencode: the run's budget ({meter.ledger.caps.describe()}) is "
                        "exhausted: stopped; its text is a partial answer, not an answer\n"
                    )
                return BUDGET_EXIT_CODE
            if meter.unmeasured:
                with stderr_log.open("a", encoding="utf-8") as stderr_stream:
                    stderr_stream.write(
                        "opencode: a finished step reported no usage for the run's budget "
                        f"({meter.ledger.caps.describe()}): what is left is unknown, stopped\n"
                    )
                return 1
```

  In `OpenCodeProvider.run`, build the meter before the call and pass it (`meter=meter,`
  after `workspace=workspace,`):

```python
        tripwire = armed_run(workspace)
        meter = StepMeter(spec.budget) if spec.budget is not None else None
```

  then, after `tokens, cost = telemetry(spec.events_log)`:

```python
        failure_reason: str | None = None
        partial_text: str | None = None
        if meter is not None and not git_tampered:
            # A tripwire's own code outranks a budget stop (spec 2026-10-13 usage §3.6).
            if exit_code == BUDGET_EXIT_CODE:
                failure_reason = BUDGET_EXHAUSTED
                partial_text = report_text(spec.events_log) or None
            elif exit_code == 1 and meter.unmeasured:
                failure_reason = BUDGET_UNMEASURED
        usage_complete = not unfinished_step(spec.events_log) and not (
            meter is not None and meter.unmeasured
        )
```

  and the `RunResult(...)` passed to `record` ends:

```python
                workspace=workspace_summary(workspace, git_tampered),
                context=context,
                partial_text=partial_text,
                failure_reason=failure_reason,
                usage_complete=usage_complete,
            ),
        )
```

- [ ] **Step 4: Run the tests** — `uv run pytest tests/unit/headless_agents/test_provider_opencode.py tests/unit/headless_agents/test_spec.py -q` → all pass; then run the real-child class five times in a row, `for i in 1 2 3 4 5; do uv run pytest tests/unit/headless_agents/test_provider_opencode.py -q -k Budget || break; done`, to see the timing is not flaky.

- [ ] **Step 5: Lint and type-check** — `uv run ruff format src tests && uv run ruff check src tests && uv run mypy src` exit 0.

- [ ] **Step 6: Commit**

```sh
git add src/headless_agents/spec.py
git add src/headless_agents/providers/opencode.py
git add tests/unit/headless_agents/test_provider_opencode.py
git commit -m "✨ feat(opencode): stop a capped run at the step that reaches its cap"
```

---

### Task 8: The engine runs a capped role — ledger, records, reasons, and the labelled partial

**Files:**
- Modify: `src/headless_agents/engine.py` — the `.budget` import, `_spec_for` (813-847),
  `_run_links` (850-896), new `_crossing`/`_budget`/`_failed_status` before
  `_configuration_line` (899), `execute`'s read path (1646, 1690-1745)
- Modify: `src/headless_agents/cli.py` — the `.capability` import (41), the end of `_run`
  (918-925) and a new `_partial_answer` after it
- Test: `tests/unit/headless_agents/test_engine_execute.py` (imports 18, append after 1200)

**Interfaces:**
- Consumes: `Plan.caps` (Task 3); `RunSpec.budget`, `RunResult` fields (Tasks 4, 7);
  `step_entry(..., steps_after_crossing=...)` (Task 4); `Ledger`, `BUDGET_EXHAUSTED`.
- Produces:
  - `_spec_for(..., budget: Ledger | None = None) -> RunSpec`
  - `_run_links(..., ledger: Ledger | None = None) -> RunResult`
  - `_crossing(ledger: Ledger | None, result: RunResult) -> int | None`, `_budget(ledger: Ledger | None) -> dict[str, object] | None`, `_failed_status(failure_reason: str | None) -> str`
  - in `execute`: `ledger = Ledger(plan.caps) if plan.caps.capped else None` (used by Tasks 9 and 10)
  - `cli._partial_answer(report: Mapping[str, object]) -> str | None`

- [ ] **Step 1: Write the failing tests.** In `test_engine_execute.py`, the first import
  becomes `from headless_agents import cli, engine, locks, proof_state, registry`, followed
  by `from headless_agents.budget import BUDGET_EXHAUSTED, BUDGET_UNMEASURED, Ledger` and
  `from headless_agents.capability import BUDGET_EXIT_CODE`. Append at the end:

```python
# ── budget caps (spec 2026-10-13 usage and budget §3.6) ─────────────────────


@dataclass
class _Spender:
    """A capped rail's stand-in: it charges the run's ledger as opencode's meter does, and
    stops with exit 7 and its partial once the run's spend reaches a cap. ``unmeasured``
    stands for a finish without the measure its cap needs; ``complete`` false for a call
    killed in flight."""

    name: str
    cost: float = 0.5
    input_tokens: int = 1000
    partial: str = "half of an answer"
    complete: bool = True
    unmeasured: bool = False
    specs: list[RunSpec] = field(default_factory=list)

    def run(self, spec: RunSpec) -> RunResult:
        self.specs.append(spec)
        ledger = spec.budget
        crossed = False
        if ledger is not None:
            ledger.measure(cost=self.cost, input_tokens=self.input_tokens)
            if self.unmeasured or not self.complete:
                ledger.complete = False
            ledger.settle()
            crossed = ledger.crossed
        code, reason = 0, None
        if crossed:
            code, reason = BUDGET_EXIT_CODE, BUDGET_EXHAUSTED
        elif self.unmeasured:
            code, reason = 1, BUDGET_UNMEASURED
        spec = spec.with_run_dir_defaults()
        return record(
            spec,
            RunResult(
                exit_code=code,
                provider=self.name,
                model=spec.model,
                report_path=spec.report_log,
                events_log=spec.events_log,
                tokens=TokenUsage(input=self.input_tokens, output=10),
                duration_seconds=0.1,
                tool_call_completed=False,
                text="the answer" if code == 0 else None,
                run_id=run_id_of(spec),
                cost_usd=self.cost,
                partial_text=self.partial if crossed else None,
                failure_reason=reason,
                usage_complete=self.complete and not self.unmeasured,
            ),
        )


def _run_json(outcome: engine.Outcome) -> dict[str, object]:
    return json.loads((outcome.run_dir / "run.json").read_text())


def test_a_capped_run_hands_its_one_ledger_to_its_step(world: World) -> None:
    spender = world.fakes["opencode"] = _Spender("opencode", cost=0.25)  # type: ignore[assignment]
    outcome = world.run("opencode", max_cost=1.0)
    assert outcome.exit_code == 0, world.said
    (spec,) = spender.specs
    assert isinstance(spec.budget, Ledger) and spec.budget.caps.max_cost == 1.0
    report = _run_json(outcome)
    assert (report["status"], report["failure_reason"]) == ("answered", None)
    assert report["budget"] == {
        "max_cost": 1.0,
        "max_input_tokens": None,
        "spent_cost": 0.25,
        "spent_input_tokens": 1000,
        "spent_complete": True,
        "overshoot_cost": 0.0,
        "overshoot_input_tokens": None,
    }
    uncapped = world.run("opencode")
    assert spender.specs[1].budget is None
    assert _run_json(uncapped)["budget"] is None


def test_a_step_reaching_the_cap_ends_the_run_budget_exhausted(world: World) -> None:
    world.fakes["opencode"] = _Spender("opencode", cost=0.75)  # type: ignore[assignment]
    outcome = world.run("opencode", max_cost=0.5)
    assert outcome.exit_code == BUDGET_EXIT_CODE
    report = _run_json(outcome)
    assert (report["status"], report["exit_code"], report["text"]) == (
        "budget_exhausted",
        7,
        None,
    )
    assert report["failure_reason"] == "budget_exhausted"
    (step,) = report["steps"]  # type: ignore[misc]
    assert (step["exit_code"], step["partial_answer"], step["failure_reason"]) == (
        7,
        "half of an answer",
        "budget_exhausted",
    )
    assert (step["steps_after_crossing"], step["started"]) == (0, True)
    assert report["budget"]["overshoot_cost"] == 0.25  # type: ignore[index]
    assert world.registry().resolve(outcome.run_id).status == "budget_exhausted"


def test_a_chain_whose_first_link_hits_the_cap_never_starts_the_second(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Admission would refuse codex (it reports usage only at the end): pretend it can.
    monkeypatch.setattr(registry, "USAGE_DURING_RUN_RAILS", frozenset({"opencode", "codex"}))
    world.roles('[pair]\nchain = ["opencode", "codex"]\n')
    world.fakes["opencode"] = _Spender("opencode", input_tokens=1000)  # type: ignore[assignment]
    outcome = world.run("pair", max_input_tokens=500)
    assert outcome.exit_code == BUDGET_EXIT_CODE
    assert "codex" not in world.fakes, "the next link was started"
    assert not any("falling back" in line for line in world.said)


def test_an_unmeasured_step_fails_the_run_with_a_lower_bound(world: World) -> None:
    world.fakes["opencode"] = _Spender("opencode", cost=0.25, unmeasured=True)  # type: ignore[assignment]
    outcome = world.run("opencode", max_cost=1.0)
    assert outcome.exit_code == 1
    report = _run_json(outcome)
    assert (report["status"], report["failure_reason"]) == ("failed", "budget_unmeasured")
    assert report["steps"][0]["failure_reason"] == "budget_unmeasured"  # type: ignore[index]
    budget = report["budget"]
    assert isinstance(budget, dict)
    assert (budget["spent_cost"], budget["spent_complete"], budget["overshoot_cost"]) == (
        0.25,
        False,
        None,
    )
    assert (report["cost_complete"], report["tokens_complete"]) == (False, False)
    assert world.said[-1] == (
        f"run {outcome.run_id}: exit 1, cost ≥ 0.25 USD (incomplete), "
        "input ≥ 1.0k, output ≥ 10 (incomplete)"
    )


def test_a_crossing_with_a_call_killed_in_flight_records_null_overshoots(world: World) -> None:
    world.fakes["opencode"] = _Spender("opencode", cost=0.75, complete=False)  # type: ignore[assignment]
    outcome = world.run("opencode", max_cost=0.5)
    assert outcome.exit_code == BUDGET_EXIT_CODE
    report = _run_json(outcome)
    budget = report["budget"]
    assert isinstance(budget, dict)
    assert (budget["spent_complete"], budget["overshoot_cost"]) == (False, None)
    assert world.said[-1].startswith(f"run {outcome.run_id}: exit 7, cost ≥ 0.75 USD (incomplete)")


def _cli_run(world: World, *argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(
        list(argv),
        environ={"PATH": "/usr/bin:/bin", "HOME": str(world.home)},
        stdin=io.StringIO(""),
        stdout=out,
        stderr=err,
        cwd=world.repo,
        home=world.home,
    )
    return code, out.getvalue(), err.getvalue()


def test_ha_run_prints_no_answer_and_the_labelled_partial_on_stderr(world: World) -> None:
    world.fakes["opencode"] = _Spender("opencode", cost=0.75)  # type: ignore[assignment]
    code, out, err = _cli_run(world, "run", "opencode", "--max-cost", "0.5", "task")
    assert (code, out) == (BUDGET_EXIT_CODE, "")
    assert "(budget_exhausted)" in err
    assert err.endswith(
        "ha: budget exhausted; partial answer follows (not an answer):\nhalf of an answer\n"
    )


def test_ha_run_json_carries_the_partial_in_its_step(world: World) -> None:
    world.fakes["opencode"] = _Spender("opencode", cost=0.75)  # type: ignore[assignment]
    code, out, err = _cli_run(world, "run", "opencode", "--max-cost", "0.5", "--json", "task")
    assert code == BUDGET_EXIT_CODE
    report = json.loads(out)
    assert report["steps"][0]["partial_answer"] == "half of an answer"
    assert report["text"] is None and "partial answer follows" not in err
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/unit/headless_agents/test_engine_execute.py -q`
Expected: 7 FAIL — the spender gets `spec.budget is None`, so the ledger tests fail on
`isinstance(spec.budget, Ledger)`, `0 == 7` or a `budget` of `None`; the unmeasured test on
`('failed', None) == ('failed', 'budget_unmeasured')`; the CLI tests on the exit code and
the missing labelled partial.

- [ ] **Step 3: Implement.** In `engine.py` the budget import becomes `from .budget import
  BUDGET_EXHAUSTED, Caps, Ledger, cost_cap, input_tokens_cap`. `_spec_for` gains the
  keyword `budget: Ledger | None = None` after `workspace` and passes `budget=budget,` after
  `output_schema=plan.request.output_schema,` in its `RunSpec(...)`. `_run_links` gains the
  keyword `ledger: Ledger | None = None` after `attempts`, its docstring and `run_one`
  become:

```python
    """The 0.4.0 chain, unchanged: walk the role's links on 3 and 4 (§3.4). A capped run's
    ``ledger`` goes to every link; a budget stop (7) is no fallback code, so the chain
    stops there by construction (spec 2026-10-13 usage, D7)."""
    providers = plan.role.providers
    chained = len(providers) > 1
    results: dict[str, RunResult] = {}

    def link_dir(provider: str) -> Path:
        return (
            step_dir / "links" / f"{providers.index(provider)}-{provider}" if chained else step_dir
        )

    def run_one(provider: str) -> int:
        spec = _spec_for(
            provider,
            plan,
            bundle,
            run_id=run_id,
            run_dir=link_dir(provider),
            workspace=workspace,
            budget=ledger,
        )
```

  Before `def _configuration_line` add:

```python
def _crossing(ledger: Ledger | None, result: RunResult) -> int | None:
    """``steps_after_crossing`` of the step stopped on the cap; ``None`` for any other."""
    if ledger is None or result.failure_reason != BUDGET_EXHAUSTED:
        return None
    return ledger.steps_after_crossing


def _budget(ledger: Ledger | None) -> dict[str, object] | None:
    """``run.json``'s ``budget``: the ledger's record, ``None`` on an uncapped run."""
    return ledger.record() if ledger is not None else None


def _failed_status(failure_reason: str | None) -> str:
    """The final status of a run that did not answer (spec 2026-10-13 usage §3.6)."""
    return "budget_exhausted" if failure_reason == BUDGET_EXHAUSTED else "failed"
```

  In `execute`, right after `run_dir = entry.run_dir` (the line after
  `started = time.monotonic()`):

```python
        started = time.monotonic()
        run_dir = entry.run_dir
        # Spec 2026-10-13 usage §3.6: one ledger for the whole run, one spender at a time.
        ledger = Ledger(plan.caps) if plan.caps.capped else None
```

  and in the read path at the end of `execute`, pass `ledger=ledger,` to `_run_links(...)`
  after `attempts=earlier,`, then:

```python
                attempts=earlier,
                steps_after_crossing=_crossing(ledger, final),
            ),
        )
        exit_code, failure_reason = _schema_outcome(request, final, say)
        if failure_reason is None:
            # Spec 2026-10-13 usage §3.6: the budget meter's reason, where it was null.
            failure_reason = final.failure_reason
        status = "answered" if exit_code == 0 else _failed_status(failure_reason)
        report.update(
            status=status,
            exit_code=exit_code,
            text=final.text,
            failure_reason=failure_reason,
            budget=_budget(ledger),
            duration_seconds=round(time.monotonic() - started, 3),
        )
```

  In `cli.py` the import becomes `from .capability import BUDGET_EXIT_CODE,
  INVALID_USAGE_EXIT_CODE`, and the end of `_run` (after the `io.say(f"{provider} exited
  ...")` block):

```python
    partial = _partial_answer(report) if outcome.exit_code == BUDGET_EXIT_CODE else None
    if partial is not None and not args.json:
        # Spec 2026-10-13 usage §3.6: stdout holds answers only; the partial goes to
        # stderr, labelled so that no reader takes it for one.
        io.say("budget exhausted; partial answer follows (not an answer):")
        io.stderr.write(partial if partial.endswith("\n") else partial + "\n")
    return outcome.exit_code


def _partial_answer(report: Mapping[str, object]) -> str | None:
    """The partial answer of the step that stopped the run on its budget, if any."""
    steps = report.get("steps")
    for step in reversed(steps if isinstance(steps, list) else []):
        partial = step.get("partial_answer") if isinstance(step, dict) else None
        if isinstance(partial, str) and partial.strip():
            return partial
    return None


# ── ha roles ────────────────────────────────────────────────────────────────
```

- [ ] **Step 4: Run the tests** — `uv run pytest tests/unit -q` → all pass (the whole suite: `_spec_for` and `_run_links` are shared by every path).

- [ ] **Step 5: Lint and type-check** — `uv run ruff format src tests && uv run ruff check src tests && uv run mypy src` exit 0.

- [ ] **Step 6: Commit**

```sh
git add src/headless_agents/engine.py
git add src/headless_agents/cli.py
git add tests/unit/headless_agents/test_engine_execute.py
git commit -m "✨ feat(engine): run a capped role on one ledger and record its budget"
```

---

### Task 9: A capped review — members in turn, nothing after a budget stop, no verdict

**Files:**
- Modify: `src/headless_agents/engine.py` — imports (`.budget`, `.capability`, the
  `.report` list at 58-67), `_run_phase` (1262-1354), `_execute_review` (1357-1498),
  `execute`'s `_execute_review(...)` call (1667-1680)
- Test: `tests/unit/headless_agents/test_review.py` (imports 23, append after 1330)

**Interfaces:**
- Consumes: `ledger` in `execute`, `_crossing`, `_budget` (Task 8); `skipped_step_entry`
  (Task 4); `BUDGET_REASONS`, `BUDGET_EXHAUSTED`, `BUDGET_EXIT_CODE` (Task 1).
- Produces: `_run_phase(..., ledger: Ledger | None = None) -> _Phase` (sequential when
  capped; `_Phase.results` holds `None` for a never-started step);
  `_execute_review(..., ledger: Ledger | None = None) -> Outcome`.

- [ ] **Step 1: Write the failing tests.** In `test_review.py` add, after the
  `from headless_agents import engine, lineage, ...` line: `from headless_agents.budget
  import BUDGET_EXHAUSTED, BUDGET_UNMEASURED` and `from headless_agents.capability import
  BUDGET_EXIT_CODE`. Append at the end:

```python
# ── a capped review (spec 2026-10-13 usage and budget §3.6) ─────────────────

OC_ROLES = """
[oc-a]
provider = "opencode"

[oc-b]
provider = "opencode"

[oc-judge]
provider = "opencode"
"""

OC_WORKFLOWS = """
[oc-panel]
shape = "review"
review = ["oc-a", "oc-b"]
judge = "oc-judge"
"""


@dataclass
class _PanelSpender:
    """opencode under a cap, call after call: call ``i`` charges ``costs[i]`` to the run's
    ledger, writes its seat's ``events.jsonl`` and ``stderr.log``, and stops with exit 7
    and a partial once the run's spend reaches the cap. ``unmeasured_call`` stands for a
    finish without the measure its cap needs. ``most_active`` counts overlapping calls."""

    name: str
    costs: list[float]
    unmeasured_call: int | None = None
    specs: list[RunSpec] = field(default_factory=list)
    active: int = 0
    most_active: int = 0

    def run(self, spec: RunSpec) -> RunResult:
        call = len(self.specs)
        self.specs.append(spec)
        self.active += 1
        self.most_active = max(self.most_active, self.active)
        try:
            time.sleep(0.2)  # long enough for a concurrent member to overlap
            ledger, cost = spec.budget, self.costs[call]
            crossed = unmeasured = False
            if ledger is not None:
                ledger.measure(cost=cost, input_tokens=None)
                unmeasured = call == self.unmeasured_call
                if unmeasured:
                    ledger.complete = False
                ledger.settle()
                crossed = ledger.crossed
            spec = spec.with_run_dir_defaults()
            assert spec.events_log is not None and spec.stderr_log is not None
            spec.events_log.parent.mkdir(parents=True, exist_ok=True)
            spec.events_log.write_text(f'{{"call": {call}}}\n', encoding="utf-8")
            spec.stderr_log.write_text(f"call {call}\n", encoding="utf-8")
            judging = "Judge the reviews below" in spec.prompt
            answer = "One reviewer is right.\nVERDICT: APPROVE" if judging else APPROVE
            code, reason = 0, None
            if crossed:
                code, reason = BUDGET_EXIT_CODE, BUDGET_EXHAUSTED
            elif unmeasured:
                code, reason = 1, BUDGET_UNMEASURED
            return record(
                spec,
                RunResult(
                    exit_code=code,
                    provider=self.name,
                    model=spec.model,
                    report_path=spec.report_log,
                    events_log=spec.events_log,
                    tokens=TokenUsage(input=100, output=10),
                    duration_seconds=0.2,
                    tool_call_completed=False,
                    text=answer if code == 0 else None,
                    run_id=run_id_of(spec),
                    cost_usd=cost,
                    partial_text=f"partial of call {call}" if crossed else None,
                    failure_reason=reason,
                    usage_complete=not unmeasured,
                ),
            )
        finally:
            self.active -= 1


def _oc_panel(world: World, *costs: float, unmeasured_call: int | None = None) -> _PanelSpender:
    config = world.home / ".config" / "ha"
    with (config / "roles.toml").open("a") as stream:
        stream.write(OC_ROLES)
    with (config / "workflows.toml").open("a") as stream:
        stream.write(OC_WORKFLOWS)
    spender = _PanelSpender("opencode", list(costs), unmeasured_call=unmeasured_call)
    world.agents["opencode"] = spender  # type: ignore[assignment]
    world.commit_by_hand()
    return spender


def test_a_capped_review_runs_its_members_one_after_another_in_their_seats(
    world: World,
) -> None:
    spender = _oc_panel(world, 0.25, 0.25, 0.25)
    outcome = world.review("oc-panel", max_cost=1.0)
    assert outcome.exit_code == 0, world.said
    assert spender.most_active == 1, "a capped panel ran two members at once"
    for seat in ("01-review-oc-a", "02-review-oc-b", "03-judge-oc-judge"):
        step_dir = outcome.run_dir / "steps" / seat
        assert (step_dir / "events.jsonl").read_text(encoding="utf-8").startswith('{"call"')
        assert (step_dir / "stderr.log").read_text(encoding="utf-8").startswith("call ")
    budget = _report(outcome)["budget"]
    assert isinstance(budget, dict) and budget["spent_cost"] == 0.75


def test_an_uncapped_review_still_runs_its_members_concurrently(world: World) -> None:
    spender = _oc_panel(world, 0.25, 0.25, 0.25)
    outcome = world.review("oc-panel")
    assert outcome.exit_code == 0, world.said
    assert spender.most_active == 2
    assert _report(outcome)["budget"] is None


def test_a_first_member_crossing_the_cap_starts_neither_the_second_nor_the_judge(
    world: World,
) -> None:
    spender = _oc_panel(world, 1.5, 0.25, 0.25)
    outcome = world.review("oc-panel", max_cost=1.0)
    assert outcome.exit_code == BUDGET_EXIT_CODE
    assert len(spender.specs) == 1
    report = _report(outcome)
    assert (report["status"], report["verdict"], report["text"]) == (
        "budget_exhausted",
        None,
        None,
    )
    assert report["failure_reason"] == "budget_exhausted"
    steps = report["steps"]
    assert isinstance(steps, list)
    assert [(s["slot"], s["exit_code"], s["started"], s["failure_reason"]) for s in steps] == [
        ("review", 7, True, "budget_exhausted"),
        ("review", None, False, "budget_exhausted"),
        ("judge", None, False, "budget_exhausted"),
    ]
    assert steps[0]["partial_answer"] == "partial of call 0"
    assert not reviews.result_path(world.state, outcome.run_id).exists()
    assert world.registry().resolve(outcome.run_id).status == "budget_exhausted"
    assert report["cleanup"] == {"status": "done"}


def test_a_second_member_crossing_the_cap_leaves_the_judge_unstarted(world: World) -> None:
    spender = _oc_panel(world, 0.75, 0.75, 0.25)
    outcome = world.review("oc-panel", max_cost=1.0)
    assert outcome.exit_code == BUDGET_EXIT_CODE
    assert len(spender.specs) == 2
    steps = _report(outcome)["steps"]
    assert isinstance(steps, list)
    assert [(s["exit_code"], s["verdict"], s["started"]) for s in steps] == [
        (0, "approve", True),
        (7, None, True),
        (None, None, False),
    ]


def test_an_unmeasured_member_fails_the_review_and_stops_it(world: World) -> None:
    spender = _oc_panel(world, 0.25, 0.25, 0.25, unmeasured_call=0)
    outcome = world.review("oc-panel", max_cost=1.0)
    assert outcome.exit_code == 1 and len(spender.specs) == 1
    report = _report(outcome)
    assert (report["status"], report["failure_reason"]) == ("failed", "budget_unmeasured")
    steps = report["steps"]
    assert isinstance(steps, list)
    assert [s["failure_reason"] for s in steps] == ["budget_unmeasured"] * 3
    budget = report["budget"]
    assert isinstance(budget, dict) and budget["spent_complete"] is False


def test_ha_show_prints_the_members_partial_and_no_verdict(world: World) -> None:
    _oc_panel(world, 1.5, 0.25, 0.25)
    outcome = world.review("oc-panel", max_cost=1.0)
    code, out, _ = _cli(world, "show", outcome.run_id)
    assert code == 0
    lines = out.splitlines()
    assert lines[0].endswith("exit 7  budget exhausted (budget_exhausted)")
    assert "--- review oc-a: partial answer (budget exhausted, not an answer) ---" in lines
    assert "partial of call 0" in lines
    assert "step    review oc-b: not started, budget_exhausted" in lines
    assert not any("VERDICT" in line for line in lines)
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/unit/headless_agents/test_review.py -q`
Expected: 5 FAIL — `assert 2 == 1` (`a capped panel ran two members at once`), `0 == 7`
and `0 == 1` (the members get no ledger), and the `ha show` header still `exit 0
approved`. `test_an_uncapped_review_still_runs_its_members_concurrently` is the
non-regression guard of spec §4 "an uncapped review still runs concurrently": it passes
before and after this task.

- [ ] **Step 3: Implement.** The imports become `from .budget import BUDGET_EXHAUSTED,
  BUDGET_REASONS, Caps, Ledger, cost_cap, input_tokens_cap` and `from .capability import
  BUDGET_EXIT_CODE, scoped_environment`; add `skipped_step_entry,` after
  `refused_step_entry,` in the `.report` import. Replace `_run_phase` entirely with:

```python
def _run_phase(
    plan: Plan,
    steps: Sequence[tuple[int, SlotPlan, str]],
    *,
    run_id: str,
    run_dir: Path,
    worktree: Path,
    say: Callable[[str], None],
    ledger: Ledger | None = None,
) -> _Phase:
    """Run ``(index, slot, prompt)`` steps in parallel, each read-only on the worktree.

    Every prompt is size-checked first, now that the diff is known (§3.4): one
    too large refuses the whole phase before anything starts, its step recorded
    with code ``2``. Each step must answer -- exit ``0`` with a non-empty text --
    or the phase fails (§3.5 step 3).

    Under a budget (spec 2026-10-13 usage §3.6) the steps run ONE AT A TIME, each in its
    own seat as in parallel: one spender, so no member spends while what is left is
    unknown. Once a step stops on the budget, no later one starts: each is recorded
    never started, with the reason, and the phase fails with it.
    """
    bundles = {index: _bundle(slot.role, plan.request, worktree) for index, slot, _ in steps}
    for index, slot, prompt in steps:
        try:
            _check_prompt_size(slot.role, prompt, bundles[index])
        except UsageError as exc:
            say(f"step {index} {slot.slot} {slot.role.name}: refused ({exc})")
            name = step_dir_name(index, slot.slot, slot.role.name)
            entry = refused_step_entry(
                index=index, slot=slot.slot, role=slot.role.name, step_dir=f"steps/{name}"
            )
            return _Phase(entries=[entry], results=[None], failure_reason="prompt_too_large")

    # A library caller's say need not be thread-safe: the steps take turns.
    said_lock = threading.Lock()

    def said(message: str) -> None:
        with said_lock:
            say(message)

    attempts_of: dict[int, list[RunResult]] = {}

    live = procgroup.Collected()

    def run_one(step: tuple[int, SlotPlan, str]) -> RunResult:
        index, slot, prompt = step
        earlier: list[RunResult] = []
        attempts_of[index] = earlier
        name = step_dir_name(index, slot.slot, slot.role.name)
        with procgroup.collecting(live):
            final = _run_links(
                _slot_run_plan(plan, slot, prompt),
                bundles[index],
                run_id=run_id,
                step_dir=run_dir / "steps" / name,
                workspace=Workspace(path=worktree),
                say=said,
                index=index,
                slot=slot.slot,
                attempts=earlier,
                ledger=ledger,
            )
        said(f"step {index} {slot.slot} {slot.role.name}: exit {final.exit_code}")
        return final

    results: list[RunResult | None]
    stopped: str | None = None
    if ledger is not None:
        results = []
        try:
            for step in steps:
                if stopped is not None:
                    results.append(None)
                    continue
                ran = run_one(step)
                results.append(ran)
                if ran.failure_reason in BUDGET_REASONS:
                    stopped = ran.failure_reason
        except BaseException:
            procgroup.kill_collected(live)
            raise
    else:
        pool = ThreadPoolExecutor(max_workers=len(steps))
        futures = [pool.submit(run_one, step) for step in steps]
        try:
            results = [future.result() for future in futures]
        except BaseException:
            # A Ctrl-C lands here, in the main thread; the rails wait in the workers and
            # never see it. Their providers die first, then their threads are joined --
            # only then may the caller remove the worktree they were reading (§3.9).
            procgroup.kill_collected(live)
            pool.shutdown(wait=True, cancel_futures=True)
            raise
        pool.shutdown(wait=True)
    entries = []
    failed = False
    for (index, slot, _), result in zip(steps, results, strict=True):
        name = step_dir_name(index, slot.slot, slot.role.name)
        if result is None:
            assert stopped is not None
            entries.append(
                skipped_step_entry(
                    index=index,
                    slot=slot.slot,
                    role=slot.role.name,
                    step_dir=f"steps/{name}",
                    reason=stopped,
                )
            )
            continue
        entry = step_entry(
            index=index,
            slot=slot.slot,
            role=slot.role.name,
            step_dir=f"steps/{name}",
            result=result,
            tools=tool_counts(result),
            model_source=slot.model_sources[result.provider],
            effort=slot.role.effort,
            timeout_seconds=slot.role.timeout,
            attempts=attempts_of.get(index, []),
            steps_after_crossing=_crossing(ledger, result),
        )
        entry["verdict"] = read_verdict(result.text)
        entries.append(entry)
        failed = failed or result.exit_code != 0 or not (result.text or "").strip()
    reason = stopped if stopped is not None else ("step_failed" if failed else None)
    return _Phase(entries=entries, results=results, failure_reason=reason)
```

  In `_execute_review`, add the keyword `ledger: Ledger | None = None` after
  `admission_wait`, append to its docstring ` Under a budget the reviewers take turns, and a
  budget stop leaves the judge unstarted and the review without a verdict (spec 2026-10-13
  usage §3.6).`, and pass `ledger=ledger,` after `say=say,` to BOTH `_run_phase(...)`
  calls. After the judge's `if failure is None and judge is not None:` block (it ends with
  `deciding = judged.results[0]`), add the never-started judge:

```python
        elif failure in BUDGET_REASONS and judge is not None:
            index, slot = judge
            name = step_dir_name(index, slot.slot, slot.role.name)
            entries.append(
                skipped_step_entry(
                    index=index,
                    slot=slot.slot,
                    role=slot.role.name,
                    step_dir=f"steps/{name}",
                    reason=failure,
                )
            )
```

  The outcome mapping and the report update become:

```python
    elif verdict == "changes":
        status, code = "changes", review_flow.CHANGES_EXIT_CODE
    elif failure == BUDGET_EXHAUSTED:
        status, code = "budget_exhausted", BUDGET_EXIT_CODE
    else:
        status, code = "failed", 1
    for step in entries:
        report = with_step(report, step)
    report.update(
        status=status,
        exit_code=code,
        verdict=verdict,
        text=text,
        failure_reason=failure,
        budget=_budget(ledger),
```

  In `execute`, pass `ledger=ledger,` after `admission_wait=admission_wait,` to the
  `_execute_review(...)` call.

- [ ] **Step 4: Run the tests** — `uv run pytest tests/unit/headless_agents/test_review.py tests/unit/headless_agents/test_engine_execute.py -q` → all pass (the existing panel tests, `test_a_panel_runs_its_reviewers_concurrently_then_the_judge_decides` and the Ctrl-C tests at 801-890, stay green: uncapped panels keep their pool).

- [ ] **Step 5: Lint and type-check** — `uv run ruff format src tests && uv run ruff check src tests && uv run mypy src` exit 0.

- [ ] **Step 6: Commit**

```sh
git add src/headless_agents/engine.py
git add tests/unit/headless_agents/test_review.py
git commit -m "✨ feat(engine): run a capped review's members in turn and stop at the cap"
```

---

### Task 10: A capped write — residue committed, its own code and status, continuable

**Files:**
- Modify: `src/headless_agents/write_flow.py` — imports (40-41), new `_step_failure` before
  `_outcome` (917), `run_write_step`'s failed-step handling (1133-1195)
- Modify: `src/headless_agents/engine.py` — `_execute_write` (1138-1237: signature and
  docstring, its `run_links` closure, `step_entry`, `report.update`), `execute`'s
  `_execute_write(...)` call (1685-1701)
- Test: `tests/unit/headless_agents/test_implement.py` (imports 23, append after 635)

**Interfaces:**
- Consumes: `ledger`, `_crossing`, `_budget` (Task 8); `FINAL_STATUSES` with
  `budget_exhausted` (Task 5); `BUDGET_EXHAUSTED`, `BUDGET_UNMEASURED`, `BUDGET_EXIT_CODE`.
- Produces: `write_flow._step_failure(final: RunResult) -> tuple[int, str, str]`;
  `_execute_write(..., ledger: Ledger | None = None) -> Outcome`.

- [ ] **Step 1: Write the failing tests.** In `test_implement.py` add, after the
  `from headless_agents import cli, engine, ...` line: `from headless_agents.budget import
  BUDGET_EXHAUSTED, BUDGET_UNMEASURED` and `from headless_agents.capability import
  BUDGET_EXIT_CODE`. Append at the end:

```python
# ── a capped implement run (spec 2026-10-13 usage and budget §3.6) ──────────

OC_ROLE = '\n[oc-implementer]\nprovider = "opencode"\nwrite = true\n'
OC_WORKFLOW = '\n[oc-build]\nshape = "implement"\nimplement = "oc-implementer"\n'


@dataclass
class _CappedWriter:
    """opencode writing under a cap: it applies ``edit``, charges ``cost`` to the run's
    ledger, and stops with exit 7 and a partial once the spend reaches the cap;
    ``unmeasured`` stands for a finish without the measure its cap needs."""

    edit: Edit | None = _flag
    cost: float = 0.75
    unmeasured: bool = False
    specs: list[RunSpec] = field(default_factory=list)

    def run(self, spec: RunSpec) -> RunResult:
        self.specs.append(spec)
        workspace = spec.profile.workspace
        assert workspace is not None and workspace.write
        if self.edit is not None:
            self.edit(workspace.path)
        ledger, crossed = spec.budget, False
        if ledger is not None:
            ledger.measure(cost=self.cost, input_tokens=None)
            if self.unmeasured:
                ledger.complete = False
            ledger.settle()
            crossed = ledger.crossed
        code, reason = 0, None
        if crossed:
            code, reason = BUDGET_EXIT_CODE, BUDGET_EXHAUSTED
        elif self.unmeasured and ledger is not None:
            code, reason = 1, BUDGET_UNMEASURED
        spec = spec.with_run_dir_defaults()
        return record(
            spec,
            RunResult(
                exit_code=code,
                provider="opencode",
                model=spec.model,
                report_path=spec.report_log,
                events_log=spec.events_log,
                tokens=TokenUsage(input=100, output=10),
                duration_seconds=0.1,
                tool_call_completed=False,
                text="I added it" if code == 0 else None,
                run_id=run_id_of(spec),
                cost_usd=self.cost,
                partial_text="I was adding it" if crossed else None,
                failure_reason=reason,
                usage_complete=not (self.unmeasured and ledger is not None),
            ),
        )


def _capped_writer(
    world: World, monkeypatch: pytest.MonkeyPatch, **fields: object
) -> _CappedWriter:
    config = world.home / ".config" / "ha"
    with (config / "roles.toml").open("a") as stream:
        stream.write(OC_ROLE)
    with (config / "workflows.toml").open("a") as stream:
        stream.write(OC_WORKFLOW)
    writer = _CappedWriter(**fields)  # type: ignore[arg-type]
    monkeypatch.setattr(engine, "get_provider", lambda name: writer)
    return writer


def test_an_implement_step_stopped_on_the_cap_commits_its_residue(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _capped_writer(world, monkeypatch)
    outcome = world.implement("Add a flag.", target="oc-build", max_cost=0.5)
    assert outcome.exit_code == BUDGET_EXIT_CODE, world.said
    run_id = outcome.run_id
    assert _subjects(world, f"ha/{run_id}") == [
        f"chore(ha): {run_id} residue via opencode/oc-default"
    ]
    assert lineage.load(world.state, run_id).members == {run_id: "budget_exhausted"}
    report = json.loads((outcome.run_dir / "run.json").read_text())
    assert (report["status"], report["exit_code"], report["failure_reason"]) == (
        "budget_exhausted",
        7,
        "budget_exhausted",
    )
    (step,) = report["steps"]
    assert (step["partial_answer"], step["steps_after_crossing"]) == ("I was adding it", 0)
    assert report["budget"]["overshoot_cost"] == 0.25
    code, out, _ = world.cli("show", run_id, "--json")
    assert code == 0 and json.loads(out)["status"] == "budget_exhausted"


def test_a_run_stopped_on_the_cap_can_be_continued(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = _capped_writer(world, monkeypatch)
    first = world.implement("Add a flag.", target="oc-build", max_cost=0.5)
    assert first.exit_code == BUDGET_EXIT_CODE
    writer.edit = _fix
    second = world.implement("Turn it on.", target="oc-build", continue_run=first.run_id)
    assert second.exit_code == 0, world.said
    assert lineage.load(world.state, first.run_id).members == {
        first.run_id: "budget_exhausted",
        second.run_id: "committed",
    }


def test_a_budget_stop_that_changed_nothing_exits_7_and_commits_nothing(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _capped_writer(world, monkeypatch, edit=None)
    outcome = world.implement("Add a flag.", target="oc-build", max_cost=0.5)
    assert outcome.exit_code == BUDGET_EXIT_CODE
    assert _subjects(world, f"ha/{outcome.run_id}") == []
    assert lineage.load(world.state, outcome.run_id).members == {outcome.run_id: "budget_exhausted"}


def test_an_unmeasured_write_step_fails_with_its_reason(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _capped_writer(world, monkeypatch, cost=0.25, unmeasured=True)
    outcome = world.implement("Add a flag.", target="oc-build", max_cost=0.5)
    assert outcome.exit_code == 1
    report = json.loads((outcome.run_dir / "run.json").read_text())
    assert (report["status"], report["failure_reason"]) == ("failed", "budget_unmeasured")
    assert report["budget"]["spent_complete"] is False
    assert lineage.load(world.state, outcome.run_id).members == {outcome.run_id: "failed"}


def test_a_tripwire_during_a_capped_run_keeps_its_own_code(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def tamper(root: Path) -> None:
        (root / ".git").write_text("gitdir: /somewhere/else\n")

    writer = _capped_writer(world, monkeypatch, edit=tamper)
    outcome = world.implement("Tamper.", target="oc-build", max_cost=0.5)
    ledger = writer.specs[0].budget
    assert ledger is not None and ledger.crossed, "the step did reach its cap"
    assert outcome.exit_code == 1
    report = json.loads((outcome.run_dir / "run.json").read_text())
    assert (report["status"], report["failure_reason"]) == ("failed", "tripwire")
    assert lineage.load(world.state, outcome.run_id).compromised == "tripwire"
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/unit/headless_agents/test_implement.py -q`
Expected: 5 FAIL — the writer gets `spec.budget is None` (`_execute_write` passes no
ledger): exit `0 == 7`, the tripwire test on `the step did reach its cap`, the unmeasured
test on `0 == 1`.

- [ ] **Step 3: Implement.** In `write_flow.py`, after `from . import locks, provenance,
  quarantine` add `from .budget import BUDGET_EXHAUSTED, BUDGET_UNMEASURED` and `from
  .capability import BUDGET_EXIT_CODE`; before `def _outcome(` add:

```python
def _step_failure(final: RunResult) -> tuple[int, str, str]:
    """``(exit code, status, reason)`` of a write whose step failed: a budget stop keeps
    its own (spec 2026-10-13 usage and budget §3.6); anything else is ``step_failed``."""
    if final.failure_reason == BUDGET_EXHAUSTED:
        return BUDGET_EXIT_CODE, "budget_exhausted", BUDGET_EXHAUSTED
    if final.failure_reason == BUDGET_UNMEASURED:
        return 1, "failed", BUDGET_UNMEASURED
    return 1, "failed", "step_failed"
```

  In `run_write_step`, the safety outcomes above (tripwire, reflog, moved HEAD,
  `status_failed`, a refused commit) are untouched; the failed-step handling becomes:

```python
        failed_step = final.exit_code != 0
        failure_code, failure_status, failure_reason = _step_failure(final)
        if not changes.any:
            status = failure_status if failed_step else "no_change"
            _publish(write, status=status, commits=(), compromised=None)
            if failed_step:
                return _outcome(
                    failure_code, failure_status, failure_reason, write, head=head, final=final
                )
            say(f"no change in {write.worktree}; nothing committed")
            return _outcome(NO_CHANGE_EXIT_CODE, "no_change", None, write, head=head, final=final)
```

```python
            status = failure_status if failed_step else "committed"
            _publish(write, status=status, commits=commits, compromised=None)
```

```python
        if failed_step:
            return _outcome(
                failure_code,
                failure_status,
                failure_reason,
                write,
                commits=commits,
                head=head,
                final=final,
            )
```

  In `engine.py`, `_execute_write` gains the keyword `ledger: Ledger | None = None` after
  `admission_wait`, and its docstring a second sentence: `A budget stop keeps today's
  handling of a failed step's residue; only its code and status are its own (spec
  2026-10-13 usage §3.6).` Its `run_links` closure passes `ledger=ledger,` after
  `attempts=earlier,` to `_run_links(...)`; its `step_entry(...)` gains
  `steps_after_crossing=_crossing(ledger, outcome.final),` after `attempts=earlier,`; its
  `report.update(...)` gains `budget=_budget(ledger),` after `failure_reason=failure_reason,`.
  In `execute`, pass `ledger=ledger,` after `admission_wait=admission_wait,` to the
  `_execute_write(...)` call.

- [ ] **Step 4: Run the tests** — `uv run pytest tests/unit -q` → all pass.

- [ ] **Step 5: Lint and type-check** — `uv run ruff format src tests && uv run ruff check src tests && uv run mypy src` exit 0.

- [ ] **Step 6: Commit**

```sh
git add src/headless_agents/write_flow.py
git add src/headless_agents/engine.py
git add tests/unit/headless_agents/test_implement.py
git commit -m "✨ feat(write): give a write's budget stop its own code and status"
```

---

### Task 11: Documentation — exit 7, the options, the role keys, `run.json`, changelog

**Files:**
- Modify: `src/headless_agents/cli.py` — `_RUN_EPILOG` (95-121)
- Modify: `README.md` — the provider example's exit codes (76-82), the HTTP outcome table
  (466-473), after the `--output-schema` bullet (566), before the **Role language** bullet
  (753), the **Runs** bullet's key lists (859-870), **Exit codes** (900-903)
- Modify: `CHANGELOG.md` — `## Unreleased` (30-49)
- Test: `tests/unit/headless_agents/test_cli.py` (append at the end)

**Interfaces:**
- Consumes: the behaviour of Tasks 1-10; `_run_help` (Task 3).
- Produces: documentation only (spec §3.7, F-17-2).

- [ ] **Step 1: Write the failing test.** Append at the end of `test_cli.py`:

```python
def test_the_run_epilog_documents_exit_7_for_runs_and_workflows() -> None:
    """Spec 2026-10-13 usage and budget §3.7, A1."""
    help_text = _run_help()
    assert help_text.count("  7 stopped at its budget cap") == 2
    assert "partial answer on stderr, never stdout; no fallback" in help_text
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run pytest tests/unit/headless_agents/test_cli.py -q -k epilog`
Expected: FAIL, `assert 0 == 2` (no exit 7 line in the epilog yet).

- [ ] **Step 3: The run epilog.** In `_RUN_EPILOG`, after `  5 write run with no change`:

```text
  7 stopped at its budget cap (--max-cost, --max-input-tokens, a role's max_cost or
    max_input_tokens): partial answer on stderr, never stdout; no fallback
```

  and after `  5 the implementation changed nothing (implement)`:

```text
  7 stopped at its budget cap: no later step, member or judge started; an implement
    step's residue is committed and can be continued
```

- [ ] **Step 4: README.** After the provider example's comment line `#   so neither can
  prove that no tool call ever started.`:

```text
# 7 stopped at its budget cap: an opencode run given RunSpec.budget (a Ledger); what it
#   wrote so far is result.partial_text, never result.text, and no chain advances on 7.
```

  At the end of the HTTP outcome table (after the `| no model, ... | `2` | stops, nothing
  sent |` row):

```markdown
| a budget cap (`--max-cost`, `--max-input-tokens`, a role's `max_cost`/`max_input_tokens`) on any link | `2` | refused before launch: an HTTP provider reports usage only with its answer |
```

  After the `--output-schema FILE` bullet (it ends `ok.json "Answer with ok = true."`.):

```markdown
- **`--max-cost USD`** and **`--max-input-tokens N`** (spec
  `docs/specs/2026-10-13-usage-and-budget-design.md`): a budget for the whole run, alone or
  together. `USD` is a finite number greater than zero, `N` a whole number greater than zero
  counting the total input the rail reports, cache reads included; anything else exits `2`.
  The `roles.toml` keys `max_cost` and `max_input_tokens` set the same caps on a role; the
  command line wins, else the smallest value any role of the run sets (every slot role of a
  review), and a workflow accepts both options. Every link of every step must report usage
  during the run -- opencode alone today -- and `--max-cost` also needs a reported cost: a
  cap on claude, codex, agy, vibe or an HTTP provider is refused before anything is
  registered or started (exit `2`, naming the role, the link and the reason). A capped run
  runs its steps one at a time (a review's members take turns, each in its own seat), reads
  opencode's stream every 0.1 s, and once the run's spend reaches a cap kills the step at
  once and exits `7`: status `budget_exhausted`, no fallback link, no later step, member or
  judge. The spend overshoots by at most the steps that finished within one poll of the
  crossing, all recorded. What the step wrote so far is a partial answer, never the answer:
  on stderr after `ha: budget exhausted; partial answer follows (not an answer):`, in the
  step's `partial_answer`, and under a labelled block in `ha show`. A finished step that
  reports no cost (under `--max-cost`) or no input tokens (under `--max-input-tokens`) stops
  the run with exit `1` and `failure_reason: "budget_unmeasured"`. An implement step stopped
  on its cap commits its residue as any failed step does, and can be continued. Example:
  `ha run opencode -m opencode-go/glm-5.3-flash --max-input-tokens 2000000 "Review the diff."`.
```

  Before the **Role language** bullet:

```markdown
- **Role budget caps**: `max_cost = 0.5` (USD) and `max_input_tokens = 2000000` in a
  `roles.toml` role cap every run that uses the role, as `--max-cost` and
  `--max-input-tokens` do (validated the same way; a boolean is refused). A role whose links
  cannot enforce its cap is refused at each run, before anything starts.
```

  In the **Runs** bullet, the end of the run key list

```markdown
  `vendor_check`, `cleanup`, `pid`, `started_at`, `duration_seconds`, `cost_usd`,
  `cost_complete`, `tokens`, `tokens_complete` and `steps`.
```

  becomes (the sentence `` `tokens` sums each token field ...`` follows unchanged):

```markdown
  `vendor_check`, `cleanup`, `pid`, `started_at`, `duration_seconds`, `cost_usd`,
  `cost_complete`, `tokens`, `tokens_complete`, `budget` and `steps`. `budget` is `null` on
  an uncapped run, else `{max_cost, max_input_tokens, spent_cost, spent_input_tokens,
  spent_complete, overshoot_cost, overshoot_input_tokens}`: a cap not set is `null` and so
  is its overshoot, `overshoot = max(0, spent - cap)`; when a step's spend is only a lower
  bound (a finish without its measure, a call killed in flight) `spent_complete` is false,
  `spent_*` read "at least" and both overshoots are `null`.
```

  and the step key sentence's "`attempts` (one `{provider, model, exit_code, tokens,
  cost_usd}` per chain link that ran before the final link) and `dir`" becomes:

```markdown
  `tools`, `attempts` (one `{provider, model, exit_code, tokens, cost_usd}` per chain link
  that ran before the final link), `partial_answer` (what a step stopped on its budget had
  written, never an answer), `steps_after_crossing`, `started` (`false` for a step never
  started after a budget stop, whose `exit_code` is `null`), `failure_reason`
  (`budget_exhausted` or `budget_unmeasured`), `usage_complete` and `dir` -- the run-relative path of
```

  **Exit codes** becomes "... `6` a review's CHANGES verdict; `7` the run stopped at its
  budget cap (partial answer kept apart, no fallback); `124` timeout."

- [ ] **Step 5: CHANGELOG** — under `## Unreleased`, at the end of `### Added`:

```markdown
- Budget caps (spec `docs/specs/2026-10-13-usage-and-budget-design.md`, PR2, ticket
  fe73f648): `ha run --max-cost USD` and `--max-input-tokens N`, and the `roles.toml` keys
  `max_cost` and `max_input_tokens`. A cap on a link that cannot report usage during the
  run (every rail but opencode) is refused before anything starts, exit `2`. A capped run
  stops at the step that reaches its cap with the new exit code `7`
  (`capability.BUDGET_EXIT_CODE`), status `budget_exhausted`, its partial answer kept apart;
  a finish without the measure its cap needs stops it with exit `1`, reason
  `budget_unmeasured`. `budget.Caps`, `budget.Ledger`, `RunSpec.budget`,
  `registry.reports_usage_during_run`, `reports_cost`, `cap_refusal`;
  `capability.kill_process_group`; `RunResult.partial_text`, `failure_reason` and
  `usage_complete` (not in `result.json`).
- `run.json`: the run key `budget`; the step keys `partial_answer`, `steps_after_crossing`,
  `started`, `failure_reason` and `usage_complete`. `budget_exhausted` is a final status of
  the registry and of lineages.
```

  and at the end of `### Changed`:

```markdown
- A capped review runs its members one after another; an uncapped one keeps running them
  concurrently. A step that never started is recorded with `exit_code: null` and
  `started: false`, and leaves the run totals out of it. An opencode call killed in flight
  makes the run totals incomplete (`tokens_complete`/`cost_complete` false), capped or not.
```

- [ ] **Step 6: Verify**

Run: `uv run pytest tests/unit/headless_agents/test_cli.py tests/unit/headless_agents/test_readme_examples.py tests/unit/headless_agents/test_readme_synopsis.py -q` → all pass.
Run: `grep -c "max_input_tokens = 2000000" README.md` → prints `1`.
Run: `make ci` → green (lint, format, mypy, unit tests, build); then `rail check` → green.

- [ ] **Step 7: Commit**

```sh
git add src/headless_agents/cli.py
git add README.md
git add CHANGELOG.md
git add tests/unit/headless_agents/test_cli.py
git commit -m "📝 docs(budget): document the caps, the role keys, exit 7 and the budget record"
```

---

## Existing tests this plan updates

- `test_report.py::test_the_run_key_set_is_the_spec_one` — `RUN_KEYS` gains `"budget"`
  before `"steps"` (Task 4).
- `test_report.py::test_the_step_key_set_ends_with_attempts` — replaced by
  `test_the_step_key_set_ends_with_attempts_then_the_budget_keys` (Task 4).
- `test_provider_opencode.py::_install` — also stubs `kill_process_group` (Task 7).
- Followed without edit: the `RUN_KEYS` users (`test_show.py:53/143/935`,
  `test_cli.py:1493`), the `test_show.py` goldens (an older report renders as before), the
  `FINAL_STATUSES` user (`tests/live/headless_agents/test_workflows_live.py:256`), the
  README synopsis test (Task 3 adds the two options to the synopsis line).

## Execution notes

- Order is strict: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11. Tasks 4-5 and 6 are
  independent of 3 and may run before it; 8 needs 3, 4 and 7; 9 and 10 need 8.
- The worker has no network and cannot run `uv`/`pytest`: it copies the tests verbatim and
  the code blocks as given; the orchestrating session reruns red (Step 2) and green
  (Step 4), ruff and mypy, and runs `make ci` before each commit. Task 7's real-child tests
  spawn short-lived bash children (no provider, no network) and take about 2 s.
- A capped chain has a single link today: only opencode can be capped and a provider
  appears at most once in a chain. The chain tests therefore declare codex able by
  monkeypatching `registry.USAGE_DURING_RUN_RAILS` (Task 2's interface).
- Release evidence (spec D10: the capped opencode live run showing the exit 7 stop, and the
  regression replay) belongs to the release, not to this PR.
