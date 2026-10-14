"""``run.json``: the report of one run (spec 0.5.0 §3.10).

A report, never an authority: it is rebuilt from the state directory and
never read back to decide anything (§3.8.1). Written at the start
(``running``), replaced atomically after every step. ``null`` means "not
measured" or "not applicable to this target", never zero. Its key set is
pinned by a test.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Final

from .result import RunResult
from .state import publish

RUN_JSON: Final = "run.json"
#: The task, as given (spec §3.10): written once at the start of a run.
PROMPT_FILE: Final = "prompt.md"
SCHEMA: Final = 1
#: Ticket 1a76fe55: ``run.json`` and ``result.json`` both carry ``"schema": 1``, so
#: ``"kind"`` is the only thing that tells a consumer which document it is reading.
KIND: Final = "run"
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
#: The ``TokenUsage`` fields a run total sums (spec 2026-10-13 usage §3.3).
TOKEN_FIELDS: Final = ("input", "output", "fresh", "cached", "thinking")


def new_report(
    *,
    run_id: str,
    target: Mapping[str, str],
    repository: Path | None,
    pid: int,
    started_at: str,
) -> dict[str, object]:
    report: dict[str, object] = dict.fromkeys(RUN_KEYS)
    report.update(
        schema=SCHEMA,
        kind=KIND,
        run_id=run_id,
        target=dict(target),
        status="running",
        repository=str(repository) if repository is not None else None,
        pid=pid,
        started_at=started_at,
        cost_complete=True,
        tokens_complete=True,
        steps=[],
    )
    return report


def step_dir_name(index: int, slot: str, role: str) -> str:
    """``<nn>-<slot>-<role>``: the launch order on two digits (§3.10)."""
    return f"{index:02d}-{slot}-{role}"


def step_entry(
    *,
    index: int,
    slot: str,
    role: str,
    step_dir: str,
    result: RunResult,
    tools: Mapping[str, int] | None = None,
    model_source: str | None = None,
    effort: str | None = None,
    timeout_seconds: float | None = None,
    attempts: Sequence[RunResult] = (),
    steps_after_crossing: int | None = None,
) -> dict[str, object]:
    return {
        "index": index,
        "slot": slot,
        "role": role,
        "dir": step_dir,
        "provider": result.provider,
        "model": result.model,
        "model_reported": result.model_reported,
        "model_source": model_source,
        "effort": effort,
        "timeout_seconds": timeout_seconds,
        "exit_code": result.exit_code,
        "duration_seconds": result.duration_seconds,
        "tokens": asdict(result.tokens) if result.tokens is not None else None,
        "cost_usd": result.cost_usd,
        # Spec §3.11: the rail's own names; None when the rail could not measure.
        "tools": dict(tools) if tools is not None else None,
        "verdict": None,
        "attempts": [attempt_entry(attempt) for attempt in attempts],
        # Spec 2026-10-13 usage and budget §3.6: kept apart from any answer.
        "partial_answer": result.partial_text,
        "steps_after_crossing": steps_after_crossing,
        "started": True,
        "failure_reason": result.failure_reason,
        "usage_complete": result.usage_complete,
    }


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


def _count(value: int) -> str:
    if value < 1000:
        return str(value)
    if value < 999_950:
        return f"{value / 1000:.1f}k"
    return f"{value / 1_000_000:.1f}M"


def _usd(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")


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


def write_report(run_dir: Path, report: Mapping[str, object]) -> None:
    publish(run_dir / RUN_JSON, report)


__all__ = [
    "KIND",
    "PROMPT_FILE",
    "RUN_JSON",
    "RUN_KEYS",
    "SCHEMA",
    "STEP_KEYS",
    "TOKEN_FIELDS",
    "attempt_entry",
    "budget_line",
    "new_report",
    "refused_step_entry",
    "run_tokens",
    "skipped_step_entry",
    "step_dir_name",
    "step_entry",
    "summary_line",
    "with_step",
    "write_report",
]
