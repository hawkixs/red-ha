"""``ha clean --force``: the journaled retirement of an uncertain lineage (spec 0.5.4 §3)."""

# The ``world`` fixture is imported, then requested by name: that is not a redefinition.
# ruff: noqa: F811

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from headless_agents import lineage, quarantine, retire, write_flow
from headless_agents.state import Unknown
from tests.unit.headless_agents.test_write_flow import World, _edit_app, _git, world  # noqa: F401

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


NOW = "20261002T130000Z"


def _environ(world: World) -> dict[str, str]:
    return {"PATH": os.environ["PATH"], "HOME": str(world.home)}


def _compromised_write(world: World) -> str:
    """A committed write whose lineage is then compromised: worktree, branch, commits."""
    world.agent.edit = _edit_app
    outcome = world.write()
    state = lineage.load(world.state, outcome.run_id)
    lineage.save(world.state, lineage.compromise(state, "tripwire"))
    return outcome.run_id


def _inspect(world: World, owner: str, *, keep_branch: bool = False) -> retire.Journal:
    return retire.inspect(
        state=world.state,
        registry=world.registry(),
        owner=owner,
        keep_branch=keep_branch,
        environ=_environ(world),
        now=NOW,
    )


def test_inspection_records_what_the_cleanup_will_check(world: World) -> None:
    owner = _compromised_write(world)
    journal = _inspect(world, owner)
    tip = _git(world.repo, "rev-parse", f"ha/{owner}").strip()
    assert journal.tip == tip
    assert journal.worktree_registered is True
    assert journal.members == (owner,)
    assert [f.path for f in journal.files] == [
        f"runs/{owner}.json",
        f"lineages/{owner}.json",
        f"lineages/{owner}.lock",
    ]
    assert journal.files[0].sha256 == retire.sha256_of(world.state / f"runs/{owner}.json")
    assert journal.files[2].sha256 is None
    assert journal.steps == dict.fromkeys(retire.STEPS, False)


def test_a_healthy_lineage_refuses_and_names_plain_clean(world: World) -> None:
    world.agent.edit = _edit_app
    owner = world.write().run_id
    with pytest.raises(retire.RetireRefused, match=f"healthy.*ha clean {owner}"):
        _inspect(world, owner)


def test_a_quarantine_of_another_lineage_refuses(world: World) -> None:
    owner = _compromised_write(world)
    quarantine.publish(
        world.state,
        "repository",
        reason="tripwire",
        run_id="20200101T000000-00000009",
        paths=[],
        common_dir=world.common_dir(),
    )
    with pytest.raises(retire.RetireRefused, match="not a member"):
        _inspect(world, owner)


def test_its_own_quarantines_and_stale_intent_are_lifted_intent_first(world: World) -> None:
    owner = _compromised_write(world)
    for scope, common in (("repository", world.common_dir()), ("operator", None)):
        quarantine.publish(
            world.state,
            scope,
            reason="x",
            run_id=owner,
            paths=[],
            common_dir=common,  # type: ignore[arg-type]
        )
    (world.state / write_flow.UNCONFINED_INTENT).write_text(json.dumps({"run_id": owner}))
    paths = [f.path for f in _inspect(world, owner).files]
    assert paths[0] == write_flow.UNCONFINED_INTENT
    assert paths[-3:] == [
        f"quarantine/repo-{quarantine.repository_id(world.common_dir())}.json",
        "quarantine/operator.json",
        f"lineages/{owner}.lock",
    ]


def test_an_intent_of_another_run_refuses(world: World) -> None:
    owner = _compromised_write(world)
    (world.state / write_flow.UNCONFINED_INTENT).write_text(
        json.dumps({"run_id": "20200101T000000-00000009"})
    )
    with pytest.raises(retire.RetireRefused, match="unconfined intent"):
        _inspect(world, owner)


def test_lineage_paths_are_never_trusted(world: World) -> None:
    owner = _compromised_write(world)
    state = lineage.load(world.state, owner)
    lineage.save(world.state, replace(state, worktree=world.home / "elsewhere"))
    with pytest.raises(retire.RetireRefused, match="worktree"):
        _inspect(world, owner)


def test_a_member_id_is_validated_before_any_path(world: World) -> None:
    owner = _compromised_write(world)
    path = lineage.lineage_path(world.state, owner)
    document = json.loads(path.read_text())
    document["members"]["../../escape"] = "failed"
    path.write_text(json.dumps(document))
    with pytest.raises(Unknown):
        _inspect(world, owner)


def test_a_symlinked_worktree_refuses_before_any_git(world: World) -> None:
    owner = _compromised_write(world)
    wt = lineage.load(world.state, owner).worktree
    moved = wt.with_name("moved")
    wt.rename(moved)
    wt.symlink_to(moved)
    world.git_calls.clear()
    with pytest.raises(retire.RetireRefused, match="symbolic link"):
        _inspect(world, owner)
    assert world.git_calls == []


def test_a_branch_checked_out_in_another_worktree_refuses(world: World) -> None:
    owner = _compromised_write(world)
    wt = lineage.load(world.state, owner).worktree
    _git(wt, "checkout", "-q", "--detach")
    _git(world.repo, "worktree", "add", "-q", str(world.home / "other"), f"ha/{owner}")
    with pytest.raises(retire.RetireRefused, match="checked out"):
        _inspect(world, owner)
