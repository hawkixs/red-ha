"""``ha clean --force``: the journaled retirement of an uncertain lineage (spec 0.5.4 §3)."""

# The ``world`` fixture is imported, then requested by name: that is not a redefinition.
# ruff: noqa: F811

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from dataclasses import replace
from pathlib import Path

import pytest

from headless_agents import engine, lineage, quarantine, retire, write_flow
from headless_agents.engine import UsageError
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


def test_the_residue_archive_holds_untracked_files_and_no_git_ran_in_the_worktree(
    world: World,
) -> None:
    owner = _compromised_write(world)
    journal = _inspect(world, owner)
    (journal.worktree / "untracked.txt").write_text("keep me\n")
    world.git_calls.clear()
    world.git_roots.clear()
    saved = retire.save_residue(journal, state=world.state, environ=_environ(world))
    assert world.git_roots, "the bundle ran no git: the check below proves nothing"
    assert saved.steps["save_residue"] is True
    assert saved.archive is not None
    with tarfile.open(world.state / saved.archive.path) as archive:
        names = archive.getnames()
    assert "wt/untracked.txt" in names and "wt/app.py" in names
    assert not any(name == "wt/.git" or name.startswith("wt/.git/") for name in names)
    assert all(str(journal.worktree) not in " ".join(call) for call in world.git_calls)
    assert not any(retire._inside(root, journal.worktree) for root in world.git_roots)


def test_the_bundle_holds_the_branch_commits(world: World) -> None:
    owner = _compromised_write(world)
    saved = retire.save_residue(_inspect(world, owner), state=world.state, environ=_environ(world))
    assert saved.bundle is not None
    _git(world.repo, "bundle", "verify", str(world.state / saved.bundle.path))


def test_the_archive_stores_links_and_rmtree_keeps_their_targets(world: World) -> None:
    owner = _compromised_write(world)
    secret = world.home / "secret.txt"
    secret.write_text("never archived\n")
    journal = _inspect(world, owner)
    (journal.worktree / "link").symlink_to(secret)
    saved = retire.save_residue(journal, state=world.state, environ=_environ(world))
    assert saved.archive is not None
    with tarfile.open(world.state / saved.archive.path) as archive:
        member = archive.getmember("wt/link")
    assert member.issym() and member.linkname == str(secret)
    removed = retire.remove_worktree(saved, state=world.state, environ=_environ(world))
    assert removed.steps["remove_worktree"] is True
    assert secret.read_text() == "never archived\n"


def test_a_worktree_whose_git_file_was_rewritten_is_saved_and_removed(world: World) -> None:
    owner = _compromised_write(world)
    journal = _inspect(world, owner)
    (journal.worktree / ".git").write_text("gitdir: /somewhere/else\n")
    saved = retire.save_residue(journal, state=world.state, environ=_environ(world))
    removed = retire.remove_worktree(saved, state=world.state, environ=_environ(world))
    assert not journal.worktree.exists()
    assert str(journal.worktree) not in _git(world.repo, "worktree", "list")
    assert removed.steps["remove_worktree"] is True


def test_remove_worktree_is_done_when_already_removed(world: World) -> None:
    owner = _compromised_write(world)
    journal = retire.save_residue(
        _inspect(world, owner), state=world.state, environ=_environ(world)
    )
    once = retire.remove_worktree(journal, state=world.state, environ=_environ(world))
    again = retire.remove_worktree(
        replace(once, steps={**once.steps, "remove_worktree": False}),
        state=world.state,
        environ=_environ(world),
    )
    assert again.steps["remove_worktree"] is True


def test_a_worktree_missing_on_disk_but_registered_is_pruned(world: World) -> None:
    owner = _compromised_write(world)
    journal = retire.save_residue(
        _inspect(world, owner), state=world.state, environ=_environ(world)
    )
    shutil.rmtree(journal.worktree)
    assert str(journal.worktree) in _git(world.repo, "worktree", "list")
    removed = retire.remove_worktree(journal, state=world.state, environ=_environ(world))
    assert removed.steps["remove_worktree"] is True
    assert str(journal.worktree) not in _git(world.repo, "worktree", "list")


def test_no_temporary_file_is_left_when_the_write_fails(tmp_path: Path) -> None:
    target = tmp_path / "residue.tar.gz"

    def failing(path: Path) -> None:
        path.write_bytes(b"half")
        raise OSError("disk full")

    with pytest.raises(OSError, match="disk full"):
        retire._write_atomically(target, failing)
    assert list(tmp_path.iterdir()) == []


def _saved_residue(world: World) -> retire.Journal:
    owner = _compromised_write(world)
    return retire.save_residue(_inspect(world, owner), state=world.state, environ=_environ(world))


def test_a_saved_residue_is_kept_when_save_residue_runs_again(world: World) -> None:
    saved = _saved_residue(world)
    assert saved.archive is not None and saved.bundle is not None
    (saved.worktree / "app.py").unlink()
    again = retire.save_residue(saved, state=world.state, environ=_environ(world))
    assert again.archive == saved.archive and again.bundle == saved.bundle
    assert retire.sha256_of(world.state / saved.archive.path) == saved.archive.sha256


def test_a_saved_archive_is_kept_when_the_worktree_is_gone(world: World) -> None:
    saved = _saved_residue(world)
    shutil.rmtree(saved.worktree)
    again = retire.save_residue(saved, state=world.state, environ=_environ(world))
    assert again.archive == saved.archive


def test_a_stale_bundle_lock_does_not_block_a_resume(world: World) -> None:
    owner = _compromised_write(world)
    journal = _inspect(world, owner)
    directory = retire.residue_dir(world.state, owner)
    directory.mkdir(parents=True)
    (directory / ".commits.bundle.tmp.lock").write_text("")
    assert retire.save_residue(journal, state=world.state, environ=_environ(world)).bundle


def test_remove_worktree_refuses_when_the_residue_was_not_saved(world: World) -> None:
    owner = _compromised_write(world)
    journal = _inspect(world, owner)
    with pytest.raises(retire.RetireRefused, match="save_residue"):
        retire.remove_worktree(journal, state=world.state, environ=_environ(world))
    assert journal.worktree.exists()


@pytest.mark.parametrize("damage", ["missing", "altered"])
def test_remove_worktree_refuses_when_the_archive_is_not_what_was_recorded(
    world: World, damage: str
) -> None:
    saved = _saved_residue(world)
    assert saved.archive is not None
    archive = world.state / saved.archive.path
    if damage == "missing":
        archive.unlink()
    else:
        archive.write_bytes(b"altered")
    with pytest.raises(retire.RetireRefused, match="archive"):
        retire.remove_worktree(saved, state=world.state, environ=_environ(world))
    assert saved.worktree.exists()


def test_the_residue_directory_is_synced_after_the_rename(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    synced: list[Path] = []
    monkeypatch.setattr(retire, "_fsync_dir", synced.append)
    saved = _saved_residue(world)
    assert synced == [retire.residue_dir(world.state, saved.owner)] * 2


def _through_worktree(world: World, owner: str, **inspect_kwargs: bool) -> retire.Journal:
    journal = _inspect(world, owner, **inspect_kwargs)
    journal = retire.save_residue(journal, state=world.state, environ=_environ(world))
    return retire.remove_worktree(journal, state=world.state, environ=_environ(world))


def test_the_branch_is_deleted_at_its_recorded_tip(world: World) -> None:
    owner = _compromised_write(world)
    journal = retire.delete_branch(
        _through_worktree(world, owner), state=world.state, environ=_environ(world)
    )
    assert journal.steps["delete_branch"] is True
    assert (
        retire.branch_tip(world.repo, f"ha/{owner}", state=world.state, environ=_environ(world))
        is None
    )


def test_a_replaced_branch_is_never_deleted(world: World) -> None:
    owner = _compromised_write(world)
    journal = _through_worktree(world, owner)
    _git(world.repo, "branch", "-f", f"ha/{owner}", "main")
    with pytest.raises(retire.RetireRefused, match="expected .* found"):
        retire.delete_branch(journal, state=world.state, environ=_environ(world))
    assert (
        _git(world.repo, "rev-parse", f"ha/{owner}").strip()
        == _git(world.repo, "rev-parse", "main").strip()
    )


def test_a_branch_already_deleted_is_done(world: World) -> None:
    owner = _compromised_write(world)
    journal = _through_worktree(world, owner)
    _git(world.repo, "branch", "-D", f"ha/{owner}")
    done = retire.delete_branch(journal, state=world.state, environ=_environ(world))
    assert done.steps["delete_branch"] is True


def test_keep_branch_keeps_it(world: World) -> None:
    owner = _compromised_write(world)
    journal = retire.delete_branch(
        _through_worktree(world, owner, keep_branch=True),
        state=world.state,
        environ=_environ(world),
    )
    assert journal.steps["delete_branch"] is True
    assert retire.branch_tip(world.repo, f"ha/{owner}", state=world.state, environ=_environ(world))


def test_the_branch_is_kept_when_its_commits_were_not_saved(world: World) -> None:
    owner = _compromised_write(world)
    journal = _inspect(world, owner)
    with pytest.raises(retire.RetireRefused, match="save_residue"):
        retire.delete_branch(journal, state=world.state, environ=_environ(world))
    assert journal.tip == _git(world.repo, "rev-parse", f"ha/{owner}").strip()


@pytest.mark.parametrize("damage", ["missing", "altered"])
def test_the_branch_is_kept_when_the_bundle_is_not_what_was_recorded(
    world: World, damage: str
) -> None:
    saved = _saved_residue(world)
    assert saved.bundle is not None
    bundle = world.state / saved.bundle.path
    if damage == "missing":
        bundle.unlink()
    else:
        bundle.write_bytes(b"altered")
    with pytest.raises(retire.RetireRefused, match="bundle"):
        retire.delete_branch(saved, state=world.state, environ=_environ(world))
    assert saved.tip == _git(world.repo, "rev-parse", saved.branch).strip()


def test_lifting_renames_every_file_with_one_timestamp(world: World) -> None:
    owner = _compromised_write(world)
    journal = _inspect(world, owner)
    lifted = retire.lift_files(journal, state=world.state)
    assert lifted.completed is True
    assert lifted.steps["lift_files"] is True
    for entry in journal.files:
        assert not (world.state / entry.path).exists()
        assert (world.state / f"{entry.path}.lifted-{NOW}").exists()


def test_an_interrupted_lift_is_finished_by_the_next(world: World) -> None:
    owner = _compromised_write(world)
    journal = _inspect(world, owner)
    first = journal.files[0]
    os.rename(world.state / first.path, world.state / f"{first.path}.lifted-{NOW}")
    lifted = retire.lift_files(journal, state=world.state)
    assert lifted.completed is True


def test_a_file_changed_since_the_journal_refuses(world: World) -> None:
    owner = _compromised_write(world)
    journal = _inspect(world, owner)
    record = world.state / journal.files[0].path
    record.write_text(record.read_text() + " ")
    with pytest.raises(retire.RetireRefused, match="lift_files"):
        retire.lift_files(journal, state=world.state)


def test_a_symlinked_state_file_is_never_followed(world: World) -> None:
    owner = _compromised_write(world)
    journal = _inspect(world, owner)
    record = world.state / journal.files[0].path
    kept = world.state / "elsewhere.json"
    kept.write_bytes(record.read_bytes())
    record.unlink()
    record.symlink_to(kept)
    with pytest.raises(retire.RetireRefused, match="lift_files"):
        retire.lift_files(journal, state=world.state)
    assert kept.exists() and record.is_symlink()


def test_every_lift_is_synced_to_the_directory(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _compromised_write(world)
    journal = _inspect(world, owner)
    synced: list[Path] = []
    monkeypatch.setattr(retire, "_fsync_dir", synced.append)
    retire.lift_files(journal, state=world.state)
    assert len(synced) == len(journal.files)


def _force(world: World, run_id: str, **kwargs: bool) -> int:
    return engine.clean(
        run_id,
        environ=_environ(world),
        home=world.home,
        say=world.said.append,
        force=True,
        **kwargs,
    )


def test_force_retires_a_compromised_lineage_and_a_new_write_is_admitted(world: World) -> None:
    owner = _compromised_write(world)
    quarantine.publish(
        world.state,
        "repository",
        reason="tripwire",
        run_id=owner,
        paths=[],
        common_dir=world.common_dir(),
    )
    assert _force(world, owner) == 0
    journal = retire.load_journal(world.state, owner)
    assert journal is not None and journal.completed
    assert quarantine.check(world.state, world.common_dir()) is None
    world.agent.edit = _edit_app
    assert world.write().exit_code == 0


def test_a_completed_cleanup_answers_already_cleaned(world: World) -> None:
    owner = _compromised_write(world)
    assert _force(world, owner) == 0
    world.said.clear()
    assert _force(world, owner) == 0
    assert any("already cleaned" in line for line in world.said)


@pytest.mark.parametrize("step", retire.STEPS)
def test_a_crash_after_each_step_is_finished_by_the_retry(
    world: World, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    owner = _compromised_write(world)

    def crash(at: str) -> None:
        if at == step:
            raise SystemExit("crashed")

    monkeypatch.setattr(retire, "_crash_after", crash)
    with pytest.raises(SystemExit):
        _force(world, owner)
    monkeypatch.setattr(retire, "_crash_after", lambda at: None)
    assert _force(world, owner) == 0
    journal = retire.load_journal(world.state, owner)
    assert journal is not None and journal.completed


def test_a_resume_resolves_the_run_through_the_journal(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _compromised_write(world)

    def crash(at: str) -> None:
        if at == "delete_branch":
            raise SystemExit("crashed")

    monkeypatch.setattr(retire, "_crash_after", crash)
    with pytest.raises(SystemExit):
        _force(world, owner)
    journal = retire.load_journal(world.state, owner)
    assert journal is not None and journal.files[0].path == f"runs/{owner}.json"
    # A crash inside lift_files, after its first rename: the run's registry entry is
    # gone, so RUN can only be found through the journal's members.
    os.rename(
        world.state / f"runs/{owner}.json",
        world.state / f"runs/{owner}.json.lifted-{journal.lifted_at}",
    )
    monkeypatch.setattr(retire, "_crash_after", lambda at: None)
    assert _force(world, owner) == 0
    finished = retire.load_journal(world.state, owner)
    assert finished is not None and finished.completed


def test_git_tampered_leaves_the_step_undone_and_the_retry_succeeds(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from headless_agents.git_tripwire import GitTampered

    owner = _compromised_write(world)
    real = retire.git
    calls = {"n": 0}

    def flaky(root: Path, args: list[str], environ: object, **kwargs: object):  # type: ignore[no-untyped-def]
        if args[:1] == ["update-ref"] and calls["n"] == 0:
            calls["n"] += 1
            raise GitTampered("tripwire fired")
        return real(root, args, environ, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(retire, "git", flaky)
    assert _force(world, owner) == 1
    journal = retire.load_journal(world.state, owner)
    assert journal is not None and journal.steps["delete_branch"] is False
    assert _force(world, owner) == 0


def test_timeout_leaves_the_step_undone(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    owner = _compromised_write(world)

    def slow(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr(retire, "git", slow)
    assert _force(world, owner) == 1
    assert any("TimeoutExpired" in line for line in world.said)


def test_keep_branch_must_match_the_journal_on_resume(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _compromised_write(world)

    def crash(at: str) -> None:
        if at == "save_residue":
            raise SystemExit("crashed")

    monkeypatch.setattr(retire, "_crash_after", crash)
    with pytest.raises(SystemExit):
        _force(world, owner)
    monkeypatch.setattr(retire, "_crash_after", lambda at: None)
    assert _force(world, owner, keep_branch=True) == 1
    assert any("--keep-branch" in line for line in world.said)


def test_another_active_member_refuses(world: World) -> None:
    """Only RUN's lifecycle lock is taken (rank order); every other member is probed
    with locks.is_free. A held lock must come from ANOTHER process: a second LIFECYCLE
    lock in this process would trip locks._check_order, not test the probe."""
    from tests.unit.headless_agents.test_write_flow import _HOLD

    owner = _compromised_write(world)
    other = "20261002T140000-0000000c"
    path = lineage.lineage_path(world.state, owner)
    document = json.loads(path.read_text())
    document["members"][other] = "committed"
    path.write_text(json.dumps(document))
    ready = world.home / "ready-member"
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLD, str(world.registry().lifecycle_lock(other)), "ex", str(ready)]
    )
    try:
        while not ready.exists():
            time.sleep(0.02)
        assert _force(world, owner) == 1
    finally:
        holder.kill()
        holder.wait()
    assert any(f"{other} is active" in line for line in world.said)
    assert retire.load_journal(world.state, owner) is None, "nothing journaled, nothing cleaned"


def test_an_inspection_refusal_says_nothing_was_cleaned(world: World) -> None:
    owner = _compromised_write(world)
    path = lineage.lineage_path(world.state, owner)
    document = json.loads(path.read_text())
    document["branch"] = "ha/other"
    path.write_text(json.dumps(document))
    assert _force(world, owner) == 1
    assert any(line.endswith("; nothing cleaned") for line in world.said)
    assert not any("the journal" in line for line in world.said)
    assert retire.load_journal(world.state, owner) is None


def test_keep_branch_without_force_is_a_usage_error(world: World) -> None:
    with pytest.raises(UsageError, match="--keep-branch"):
        engine.clean(
            "20261002T000000-aaaaaaaa",
            environ=_environ(world),
            home=world.home,
            say=world.said.append,
            keep_branch=True,
        )
