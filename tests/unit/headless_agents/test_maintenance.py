"""Scheduled proofs exercise real subprocesses without spending provider quota."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def cycle(tmp_path: Path):
    scenario = tmp_path / "scenario.json"
    calls = tmp_path / "calls.jsonl"
    ha = tmp_path / "ha"
    ha.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
s=json.loads(Path(os.environ['SCENARIO']).read_text())
with Path(os.environ['CALLS']).open('a') as out: out.write(json.dumps(sys.argv[1:])+'\\n')
if sys.argv[1]=='providers':
 print(json.dumps(s['providers'])); sys.exit(s.get('providers_exit',0))
r=s['proofs'][sys.argv[2]]
print(r.get('raw',json.dumps(r.get('report',{}))))
sys.exit(r.get('exit',0))
""")
    ha.chmod(0o700)
    events = tmp_path / "coordination" / "events.log"

    def run(proofs=None, providers=None, discovery_exit=0):
        if providers is None:
            providers = [
                {"name": r, "available": True, "version": "cli 2.0.0"}
                for r in ("codex", "agy", "opencode")
            ]
        providers += [
            {"name": r, "available": False, "version": None}
            for r in ("codex", "agy", "opencode")
            if r not in {p["name"] for p in providers}
        ]
        scenario.write_text(
            json.dumps(
                {"providers": providers, "proofs": proofs or {}, "providers_exit": discovery_exit}
            )
        )
        env = {**os.environ, "SCENARIO": str(scenario), "CALLS": str(calls)}
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "headless_agents.maintenance",
                "--ha",
                str(ha),
                "--events",
                str(events),
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result

    return run, events, calls


def passed(rail):
    return {
        "report": {
            "schema": 1,
            "verdicts": [
                {"rail": rail, "version": "cli 2.0.0", "outcome": "passed", "recorded": True}
            ],
            "modes": {rail: "parallel"},
        }
    }


def test_stale_workers_are_proven_and_reported(cycle):
    run, events, calls = cycle
    result = run({r: passed(r) for r in ("codex", "agy", "opencode")})
    assert result.returncode == 0, result.stderr
    assert events.read_text().splitlines() == [
        "PROVE codex cli 2.0.0 passed",
        "PROVE agy cli 2.0.0 passed",
        "PROVE opencode cli 2.0.0 passed",
    ]
    assert json.loads(calls.read_text().splitlines()[1]) == ["prove", "codex", "--stale", "--json"]


def test_failure_is_visible_and_does_not_stop_other_workers(cycle):
    run, events, _ = cycle
    bad = passed("codex")
    bad["report"]["verdicts"][0]["outcome"] = "failed"
    bad["report"]["modes"]["codex"] = "refused"
    bad["exit"] = 1
    result = run({"codex": bad, "agy": passed("agy"), "opencode": passed("opencode")})
    assert result.returncode == 1
    assert events.read_text().splitlines() == [
        "PROVE codex cli 2.0.0 failed",
        "PROVE agy cli 2.0.0 passed",
        "PROVE opencode cli 2.0.0 passed",
    ]


def test_fresh_cycle_is_quiet_and_missing_workers_are_skipped(cycle):
    run, events, calls = cycle
    result = run(
        {"codex": {"report": {"schema": 1, "verdicts": [], "modes": {"codex": "parallel"}}}},
        [
            {"name": "codex", "available": True, "version": "2.0.0"},
            {"name": "agy", "available": False, "version": None},
        ],
    )
    assert result.returncode == 0, result.stderr
    assert not events.exists()
    assert len(calls.read_text().splitlines()) == 2


@pytest.mark.parametrize(
    "bad",
    [
        {"raw": "not json"},
        {"report": {}},
        {"exit": 2, "raw": ""},
        {"report": {"schema": 1, "verdicts": [], "modes": {"codex": "refused"}}},
        {
            "report": {
                "schema": 1,
                "verdicts": [
                    {"rail": "codex", "version": "2.0.0", "outcome": "passed", "recorded": False}
                ],
                "modes": {"codex": "parallel"},
            }
        },
    ],
)
def test_bad_or_unrecorded_proof_never_reports_passed(cycle, bad):
    run, events, _ = cycle
    result = run({"codex": bad}, [{"name": "codex", "available": True, "version": "2.0.0"}])
    assert result.returncode == 1
    assert events.read_text() == "PROVE codex 2.0.0 failed\n"


def test_cycle_lock_prevents_duplicate_proofs(cycle):
    run, events, calls = cycle
    events.parent.mkdir()
    with (events.parent / "worker-proof.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = run()
    assert result.returncode == 0, result.stderr
    assert not calls.exists()


def test_successful_serialised_worker_is_not_reported_as_failed(cycle):
    run, events, _ = cycle
    proof = passed("codex")
    proof["report"]["modes"]["codex"] = "writes serialised"
    result = run({"codex": proof}, [{"name": "codex", "available": True, "version": "cli 2.0.0"}])
    assert result.returncode == 0, result.stderr
    assert events.read_text() == "PROVE codex cli 2.0.0 passed\n"


def test_discovery_failure_is_visible_without_running_proofs(cycle):
    run, events, calls = cycle
    result = run(discovery_exit=2)
    assert result.returncode == 1
    assert events.read_text().splitlines() == [
        "PROVE codex unknown failed",
        "PROVE agy unknown failed",
        "PROVE opencode unknown failed",
    ]
    assert len(calls.read_text().splitlines()) == 1


def test_existing_coordination_events_survive_and_versions_cannot_inject_lines(cycle):
    run, events, _ = cycle
    events.parent.mkdir()
    events.write_text("EXISTING event\n")
    result = run(
        {"codex": passed("codex")},
        [{"name": "codex", "available": True, "version": "2.0.0\nFORGED failed"}],
    )
    assert result.returncode == 0, result.stderr
    assert events.read_text().splitlines() == [
        "EXISTING event",
        "PROVE codex 2.0.0 FORGED failed passed",
    ]


def test_next_scheduled_cycle_reproves_a_changed_worker(cycle):
    run, events, _ = cycle
    fresh = {"report": {"schema": 1, "verdicts": [], "modes": {"codex": "parallel"}}}
    assert (
        run({"codex": fresh}, [{"name": "codex", "available": True, "version": "1.0.0"}]).returncode
        == 0
    )
    assert not events.exists()
    assert (
        run(
            {"codex": passed("codex")}, [{"name": "codex", "available": True, "version": "2.0.0"}]
        ).returncode
        == 0
    )
    assert events.read_text() == "PROVE codex 2.0.0 passed\n"
