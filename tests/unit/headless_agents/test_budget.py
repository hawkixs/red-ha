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
    ledger = Ledger(Caps(max_input_tokens=1000))
    assert (ledger.complete, ledger.unmeasured, ledger.crossed) == (True, False, False)
    record = ledger.record()
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
