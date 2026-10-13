"""Live vibe runs (HA_LIVE=1, spends Mistral quota, never in CI).

Each run stays under ``faulthandler_timeout`` (120 s, which exits the whole
session): the run's own deadline is 110 s, so a slow model fails its own test.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from headless_agents.profile import CapabilityProfile, Workspace
from headless_agents.providers.vibe import VibeProvider
from headless_agents.spec import RunSpec

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("HA_LIVE") != "1", reason="live: set HA_LIVE=1"),
]
MODEL = os.environ.get("HA_VIBE_MODEL", "mistral-large-4")


def _spec(tmp_path: Path, prompt: str, workspace: Workspace) -> RunSpec:
    return RunSpec(
        prompt=prompt,
        name="ha-live-vibe",
        model=MODEL,
        profile=CapabilityProfile(workspace=workspace),
        timeout_seconds=110.0,
        max_turns=8,
        run_dir=tmp_path / "run",
    )


def test_a_read_only_review_reads_and_answers(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.txt").write_text("LIVE-MARKER-READ\n")
    result = VibeProvider().run(
        _spec(tmp_path, "Read a.txt and reply with its content.", Workspace(path=ws))
    )
    assert result.exit_code == 0
    assert "LIVE-MARKER-READ" in (result.text or "")


def test_a_write_lands_inside_and_nowhere_else(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "outside.txt"
    prompt = (
        f"Write 'live-ok' to out.txt in the workspace, then try to write 'escaped' to {outside}."
    )
    result = VibeProvider().run(_spec(tmp_path, prompt, Workspace(path=ws, write=True)))
    assert result.exit_code == 0
    assert (ws / "out.txt").read_text().strip() == "live-ok"
    assert not outside.exists()
