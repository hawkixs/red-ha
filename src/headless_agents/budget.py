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
    flight). ``unmeasured`` turns true, for good, when a finished call lacked the measure
    a cap needs: what is left is unknowable, so no later step may start.
    ``steps_after_crossing`` is ``None`` until the spend reaches a cap, then counts the
    ``step_finish`` parts the meter saw after the one that crossed.
    """

    caps: Caps
    complete: bool = True
    unmeasured: bool = False
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
