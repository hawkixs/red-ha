"""``ha clean --force``: the journaled retirement of an uncertain lineage (spec 0.5.4 §3)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from headless_agents import retire
from headless_agents.state import Unknown

OWNER = "20261002T120000-0000000a"
MEMBER = "20261002T120500-0000000b"


def _journal(tmp_path: Path, **changes: object) -> retire.Journal:
    journal = retire.Journal(
        owner=OWNER,
        members=(OWNER, MEMBER),
        repository=tmp_path / "repo",
        common_dir=tmp_path / "repo" / ".git",
        worktree=tmp_path / "runs" / OWNER / "wt",
        worktree_registered=True,
        branch=f"ha/{OWNER}",
        tip="a" * 40,
        base="b" * 40,
        keep_branch=False,
        lifted_at="20261002T121000Z",
        files=(
            retire.Lifted(f"runs/{OWNER}.json", "c" * 64),
            retire.Lifted(f"lineages/{OWNER}.lock", None),
        ),
        archive=None,
        bundle=None,
        steps=dict.fromkeys(retire.STEPS, False),
        completed=False,
    )
    return replace(journal, **changes)  # type: ignore[arg-type]


def test_a_journal_round_trips(tmp_path: Path) -> None:
    state = tmp_path / "state"
    journal = _journal(
        tmp_path, archive=retire.Saved(f"cleanups/{OWNER}/worktree.tar.gz", "d" * 64)
    )
    path = retire.journal_path(state, OWNER)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(retire.to_document(journal)))
    assert retire.load_journal(state, OWNER) == journal


def test_an_absent_journal_is_none(tmp_path: Path) -> None:
    assert retire.load_journal(tmp_path / "state", OWNER) is None


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("version", 2),
        ("owner", "20261002T120000-0000000c"),
        ("members", ["../../etc"]),
        ("branch", "main"),
        ("tip", "not-a-sha"),
        ("lifted_at", "yesterday"),
        ("files", [{"path": "../outside.json", "sha256": None}]),
        ("files", [{"path": "/etc/passwd", "sha256": None}]),
        ("steps", {"save_residue": True}),
        ("surprise", 1),
    ],
)
def test_a_journal_is_never_trusted_when_malformed(tmp_path: Path, key: str, value: object) -> None:
    state = tmp_path / "state"
    document = retire.to_document(_journal(tmp_path))
    document[key] = value
    path = retire.journal_path(state, OWNER)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(document))
    with pytest.raises(Unknown):
        retire.load_journal(state, OWNER)


def test_find_owner_reads_the_members_of_every_journal(tmp_path: Path) -> None:
    state = tmp_path / "state"
    path = retire.journal_path(state, OWNER)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(retire.to_document(_journal(tmp_path))))
    (path.parent / "garbage.json").write_text("{")
    assert retire.find_owner(state, MEMBER) == OWNER
    assert retire.find_owner(state, "20261002T130000-0000000f") is None
