"""Provenance records each link with its frozen model and switches (spec §3.3, D4)."""

from __future__ import annotations

from pathlib import Path

from headless_agents import provenance
from headless_agents.model_vendor import LinkRecord, role_links
from headless_agents.roles import implicit_role

SHA = "a" * 40


def test_a_record_carries_its_links(tmp_path: Path) -> None:
    links = (LinkRecord("codex", "gpt-6", ()), LinkRecord("opencode", "opencode-go/glm-5", ()))
    provenance.record(
        tmp_path,
        SHA,
        run_id="r",
        lineage="r",
        made_by="engine",
        providers=["codex", "opencode"],
        links=links,
    )
    stored = provenance.lookup(tmp_path, SHA)
    assert stored is not None
    assert stored["links"] == [
        {"provider": "codex", "model": "gpt-6", "switches": []},
        {"provider": "opencode", "model": "opencode-go/glm-5", "switches": []},
    ]


def test_role_links_take_the_snapshot_and_the_effective_environment() -> None:
    role = implicit_role("codex")
    (link,) = role_links(role, {"codex": "gpt-6"}, {"OPENAI_BASE_URL": "http://x"})
    assert link == LinkRecord("codex", "gpt-6", ("OPENAI_BASE_URL",))
    (bare,) = role_links(role, {}, {})
    assert bare == LinkRecord("codex", "", ())


def test_link_documents_round_trip_and_reject_malformed() -> None:
    link = LinkRecord("claude", "claude-x", ("ANTHROPIC_BASE_URL",))
    assert LinkRecord.from_document(link.to_document()) == link
    bad: object
    for bad in (
        None,
        [],
        {"provider": "", "model": "m", "switches": []},
        {"provider": "p", "model": 1, "switches": []},
        {"provider": "p", "model": "m", "switches": [""]},
        {"provider": "p", "model": "m"},
    ):
        assert LinkRecord.from_document(bad) is None
