"""run.json, the run report (spec 0.5.0 §3.10): its key set is pinned here."""

from __future__ import annotations

import json
from pathlib import Path

from headless_agents.report import (
    RUN_JSON,
    RUN_KEYS,
    STEP_KEYS,
    new_report,
    refused_step_entry,
    step_dir_name,
    step_entry,
    summary_line,
    with_step,
    write_report,
)
from headless_agents.result import RunResult, TokenUsage


def _result(**overrides: object) -> RunResult:
    fields: dict[str, object] = {
        "exit_code": 0,
        "provider": "codex",
        "model": "m",
        "report_path": None,
        "events_log": None,
        "tokens": TokenUsage(input=10, output=2),
        "duration_seconds": 1.5,
        "tool_call_completed": False,
        "cost_usd": 0.25,
    }
    fields.update(overrides)
    return RunResult(**fields)  # type: ignore[arg-type]


def _report() -> dict[str, object]:
    return new_report(
        run_id="20260925T000000-aaaaaaaa",
        target={"kind": "role", "name": "codex"},
        repository=Path("/repo"),
        pid=42,
        started_at="2026-09-25T00:00:00Z",
    )


def test_the_run_key_set_is_the_spec_one() -> None:
    assert tuple(_report()) == RUN_KEYS
    assert RUN_KEYS == (
        "schema", "kind", "run_id", "target", "status", "exit_code", "verdict", "text",
        "repository", "base", "head", "branch", "lineage", "continues", "findings_from",
        "implement_providers", "commits", "failure_reason", "vendor_check", "cleanup", "pid",
        "started_at", "duration_seconds", "cost_usd", "cost_complete", "tokens",
        "tokens_complete", "steps",
    )  # fmt: skip


def test_a_new_report_names_its_kind_as_run() -> None:
    """Ticket 1a76fe55: ``run.json`` and ``result.json`` both carry ``schema: 1``; only
    ``kind`` tells a consumer apart which one it is reading."""
    assert _report()["kind"] == "run"


def test_a_new_report_is_running_with_every_unmeasured_field_null() -> None:
    report = _report()
    assert report["schema"] == 1
    assert report["status"] == "running"
    assert report["steps"] == []
    for key in ("exit_code", "verdict", "text", "base", "head", "branch", "lineage",
                "continues", "findings_from", "implement_providers", "commits",
                "failure_reason", "vendor_check", "cleanup", "duration_seconds", "cost_usd"):  # fmt: skip
        assert report[key] is None, key


def test_the_step_key_set_is_the_spec_one() -> None:
    step = step_entry(
        index=1, slot="run", role="codex", step_dir="steps/01-run-codex", result=_result()
    )
    assert tuple(step) == STEP_KEYS
    assert step["tokens"] == {
        "input": 10,
        "output": 2,
        "fresh": None,
        "cached": None,
        "thinking": None,
    }
    assert step["tools"] is None, "tool counters arrive in lot 2: not measured is null"


def test_an_unmeasured_token_count_is_null_not_zero() -> None:
    step = step_entry(index=1, slot="run", role="codex", step_dir="d", result=_result(tokens=None))
    assert step["tokens"] is None


def test_cost_is_the_sum_and_incomplete_when_a_step_is_unmeasured() -> None:
    report = with_step(
        _report(), step_entry(index=1, slot="run", role="a", step_dir="d1", result=_result())
    )
    assert (report["cost_usd"], report["cost_complete"]) == (0.25, True)
    report = with_step(
        report,
        step_entry(index=2, slot="run", role="b", step_dir="d2", result=_result(cost_usd=None)),
    )
    assert (report["cost_usd"], report["cost_complete"]) == (0.25, False)


def test_with_step_does_not_mutate_its_input() -> None:
    report = _report()
    with_step(report, step_entry(index=1, slot="run", role="a", step_dir="d", result=_result()))
    assert report["steps"] == []


def test_step_dir_name() -> None:
    assert step_dir_name(1, "run", "codex") == "01-run-codex"
    assert step_dir_name(12, "judge", "judge") == "12-judge-judge"


def test_write_report_publishes_run_json(tmp_path: Path) -> None:
    write_report(tmp_path, _report())
    assert json.loads((tmp_path / RUN_JSON).read_text())["run_id"] == "20260925T000000-aaaaaaaa"


def test_a_step_carries_its_tool_counts() -> None:
    entry = step_entry(
        index=1,
        slot="run",
        role="codex",
        step_dir="steps/01-run-codex",
        result=_result(),
        tools={"command_execution": 4},
    )
    assert entry["tools"] == {"command_execution": 4}


def test_a_step_without_counts_is_not_measured() -> None:
    entry = step_entry(
        index=1, slot="run", role="codex", step_dir="steps/01-run-codex", result=_result()
    )
    assert entry["tools"] is None


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
        "run 20260925T000000-aaaaaaaa: exit 0, cost 0.37 USD, input 8.3M (cached 7.4M), output 5.8k"
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
