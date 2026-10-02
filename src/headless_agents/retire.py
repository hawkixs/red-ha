"""``ha clean --force``: retire an uncertain lineage through a journal (spec 0.5.4 §3).

A lineage left compromised, pending or quarantined blocks every later write on its
repository. This module retires it after inspection:

- it saves the worktree as an archive and the branch's commits as a bundle;
- it removes the worktree;
- it deletes the branch only at the tip it recorded;
- it renames the lineage's state files to ``*.lifted-<ts>``.

Every expectation is written to ``<state>/cleanups/<owner>.json`` before the first
destructive step, and every step checks "already done" before "expected pre-state".
An interrupted cleanup is finished by the next ``ha clean --force``; it is never
rolled back. No git command ever runs inside the worktree: a tripwire rewrote its
``.git``, and a tampered repository may carry filter drivers.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
import tarfile
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Final

from . import lineage as lineages
from . import locks, quarantine
from .git_tripwire import GitTampered
from .gitops import git
from .runs import RUN_ID_PATTERN, Registry, RegistryError
from .state import Unknown, _fsync_dir, create_once, ensure_dir, publish, read, read_optional
from .write_flow import UNCONFINED_INTENT

JOURNAL_DIR: Final = "cleanups"
JOURNAL_VERSION: Final = 1
STEPS: Final[tuple[str, ...]] = ("save_residue", "remove_worktree", "delete_branch", "lift_files")
LIFT_FORMAT: Final = "%Y%m%dT%H%M%SZ"
_LIFTED_AT: Final = re.compile(r"\d{8}T\d{6}Z")
_OBJECT_ID: Final = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_SHA256: Final = re.compile(r"[0-9a-f]{64}")
_KEYS: Final = frozenset(
    {
        "version",
        "owner",
        "members",
        "repository",
        "common_dir",
        "worktree",
        "worktree_registered",
        "branch",
        "tip",
        "base",
        "keep_branch",
        "lifted_at",
        "files",
        "archive",
        "tree",
        "bundle",
        "steps",
        "completed",
    }
)


class RetireRefused(Exception):  # noqa: N818 - a refusal, not a crash
    """A step found a state that is neither done nor expected: exit 1, the journal kept."""


@dataclass(frozen=True)
class Lifted:
    #: Relative to ``<state>``.
    path: str
    #: ``None`` for a lock file: its identity is its existence.
    sha256: str | None


@dataclass(frozen=True)
class Saved:
    path: str
    sha256: str


@dataclass(frozen=True)
class Journal:
    owner: str
    members: tuple[str, ...]
    repository: Path
    common_dir: Path
    worktree: Path
    worktree_registered: bool
    branch: str
    tip: str | None
    base: str | None
    keep_branch: bool
    lifted_at: str
    files: tuple[Lifted, ...]
    archive: Saved | None
    #: Digest of the worktree content the archive holds (:func:`_tree_digest`).
    tree: str | None
    bundle: Saved | None
    steps: Mapping[str, bool]
    completed: bool


def journal_path(state: Path, owner: str) -> Path:
    return state / JOURNAL_DIR / f"{owner}.json"


def residue_dir(state: Path, owner: str) -> Path:
    return state / JOURNAL_DIR / owner


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _saved(value: Saved | None) -> dict[str, str] | None:
    return None if value is None else {"path": value.path, "sha256": value.sha256}


def to_document(journal: Journal) -> dict[str, object]:
    return {
        "version": JOURNAL_VERSION,
        "owner": journal.owner,
        "members": list(journal.members),
        "repository": str(journal.repository),
        "common_dir": str(journal.common_dir),
        "worktree": str(journal.worktree),
        "worktree_registered": journal.worktree_registered,
        "branch": journal.branch,
        "tip": journal.tip,
        "base": journal.base,
        "keep_branch": journal.keep_branch,
        "lifted_at": journal.lifted_at,
        "files": [{"path": f.path, "sha256": f.sha256} for f in journal.files],
        "archive": _saved(journal.archive),
        "tree": journal.tree,
        "bundle": _saved(journal.bundle),
        "steps": dict(journal.steps),
        "completed": journal.completed,
    }


def _relative(value: object, path: Path) -> str:
    """A path under ``<state>``: relative, no ``..``, no empty part."""
    if not isinstance(value, str) or not value:
        raise Unknown(f"{path}: a recorded path is not a string")
    parts = PurePosixPath(value).parts
    if PurePosixPath(value).is_absolute() or ".." in parts or not parts:
        raise Unknown(f"{path}: {value!r} is not a path under the state directory")
    return value


def _sha(value: object, path: Path, *, optional: bool) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise Unknown(f"{path}: a recorded sha256 is malformed")
    return value


def _saved_from(value: object, path: Path) -> Saved | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        raise Unknown(f"{path}: a saved residue entry is malformed")
    sha = _sha(value["sha256"], path, optional=False)
    assert sha is not None
    return Saved(_relative(value["path"], path), sha)


def _object_id(value: object, path: Path, what: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _OBJECT_ID.fullmatch(value):
        raise Unknown(f"{path}: {what} is not a commit id")
    return value


def _text(document: Mapping[str, object], key: str, path: Path) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise Unknown(f"{path}: {key} is not a string")
    return value


def _absolute(document: Mapping[str, object], key: str, path: Path) -> Path:
    value = Path(_text(document, key, path))
    if not value.is_absolute():
        raise Unknown(f"{path}: {key} {str(value)!r} is not an absolute path")
    return value


def _flag(document: Mapping[str, object], key: str, path: Path) -> bool:
    value = document.get(key)
    if not isinstance(value, bool):
        raise Unknown(f"{path}: {key} is not a boolean")
    return value


def _parse(document: Mapping[str, object], path: Path, owner: str) -> Journal:
    if set(document) != _KEYS:
        raise Unknown(f"{path}: unexpected or missing keys")
    if document["version"] != JOURNAL_VERSION:
        raise Unknown(f"{path}: version {document['version']!r} is not {JOURNAL_VERSION}")
    if document["owner"] != owner or not RUN_ID_PATTERN.fullmatch(owner):
        raise Unknown(f"{path}: names owner {document['owner']!r}, expected {owner!r}")
    members = document["members"]
    if (
        not isinstance(members, list)
        or not all(isinstance(m, str) and RUN_ID_PATTERN.fullmatch(m) for m in members)
        or len(set(members)) != len(members)
        or owner not in members
    ):
        raise Unknown(f"{path}: members are malformed, repeated or do not list the owner")
    if _text(document, "branch", path) != f"ha/{owner}":
        raise Unknown(f"{path}: branch is not ha/{owner}")
    lifted_at = _text(document, "lifted_at", path)
    if not _LIFTED_AT.fullmatch(lifted_at):
        raise Unknown(f"{path}: lifted_at is malformed")
    files = document["files"]
    if not isinstance(files, list) or not all(
        isinstance(f, dict) and set(f) == {"path", "sha256"} for f in files
    ):
        raise Unknown(f"{path}: files are malformed")
    steps = document["steps"]
    if (
        not isinstance(steps, dict)
        or set(steps) != set(STEPS)
        or not all(isinstance(v, bool) for v in steps.values())
    ):
        raise Unknown(f"{path}: steps are malformed")
    archive = _saved_from(document["archive"], path)
    tree = _sha(document["tree"], path, optional=True)
    if (archive is None) != (tree is None):
        raise Unknown(f"{path}: the archive and the digest of its tree must be recorded together")
    return Journal(
        owner=owner,
        members=tuple(members),
        repository=_absolute(document, "repository", path),
        common_dir=_absolute(document, "common_dir", path),
        worktree=_absolute(document, "worktree", path),
        worktree_registered=_flag(document, "worktree_registered", path),
        branch=f"ha/{owner}",
        tip=_object_id(document["tip"], path, "tip"),
        base=_object_id(document["base"], path, "base"),
        keep_branch=_flag(document, "keep_branch", path),
        lifted_at=lifted_at,
        files=tuple(
            Lifted(_relative(f["path"], path), _sha(f["sha256"], path, optional=True))
            for f in files
        ),
        archive=archive,
        tree=tree,
        bundle=_saved_from(document["bundle"], path),
        steps={name: bool(steps[name]) for name in STEPS},
        completed=_flag(document, "completed", path),
    )


def load_journal(state: Path, owner: str) -> Journal | None:
    """The journal of ``owner``; ``None`` when there is none, :class:`Unknown` on doubt."""
    if not RUN_ID_PATTERN.fullmatch(owner):
        raise Unknown(f"not a lineage owner: {owner!r}")
    path = journal_path(state, owner)
    document = read_optional(path)
    if document is None:
        return None
    return _parse(document, path, owner)


def find_owner(state: Path, run_id: str) -> str | None:
    """The owner of the journal listing ``run_id`` as a member: a resume after the
    run's registry entry was lifted. A journal that does not parse is skipped here;
    loading it by its owner still refuses."""
    directory = state / JOURNAL_DIR
    if not directory.is_dir() or not RUN_ID_PATTERN.fullmatch(run_id):
        return None
    for path in sorted(directory.glob("*.json")):
        if not RUN_ID_PATTERN.fullmatch(path.stem):
            continue
        try:
            document = read(path)
        except Unknown:
            continue
        members = document.get("members")
        if isinstance(members, list) and run_id in members:
            return path.stem
    return None


def _real(path: Path) -> str:
    return os.path.realpath(path)


def _inside(path: Path, root: Path) -> bool:
    normal, top = _real(path), _real(root)
    return normal == top or normal.startswith(top.rstrip(os.sep) + os.sep)


def worktrees(
    repository: Path, branch: str, worktree: Path, *, state: Path, environ: Mapping[str, str]
) -> tuple[bool, list[str]]:
    """``(registered, others)``: whether ``worktree`` is registered, and every OTHER
    worktree that has ``refs/heads/<branch>`` checked out."""
    result = git(repository, ["worktree", "list", "--porcelain"], environ, state=state)
    if result.returncode != 0:
        raise RetireRefused(f"git worktree list failed: {result.stderr.strip()}")
    registered, others = False, []
    current: str | None = None
    for line in [*result.stdout.splitlines(), ""]:
        if line.startswith("worktree "):
            current = line[len("worktree ") :]
        elif line == f"branch refs/heads/{branch}" and current is not None:
            if _real(Path(current)) != _real(worktree):
                others.append(current)
        elif not line:
            current = None
        if current is not None and _real(Path(current)) == _real(worktree):
            registered = True
    return registered, others


def branch_tip(
    repository: Path, branch: str, *, state: Path, environ: Mapping[str, str]
) -> str | None:
    result = git(
        repository,
        ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}"],
        environ,
        state=state,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _quarantine_of(path: Path, members: Sequence[str]) -> Lifted | None:
    """The quarantine at ``path`` as a file to lift, when it names a member."""
    if not os.path.lexists(path):
        return None
    try:
        document = read(path)
    except Unknown as exc:
        raise RetireRefused(f"{path} is unreadable ({exc}): inspect it first") from None
    run_id = document.get("run_id")
    if run_id not in members:
        raise RetireRefused(
            f"{path} is the quarantine of run {run_id}, not a member of this lineage: "
            "it is not this cleanup's to lift"
        )
    return Lifted(str(path.relative_to(path.parents[1])), sha256_of(path))


def _members_of(current: lineages.LineageState, owner: str) -> tuple[str, ...]:
    """The lineage's member ids, checked before any path is built from one."""
    members = tuple(sorted(current.members))
    if not all(RUN_ID_PATTERN.fullmatch(member) for member in members):
        raise Unknown(f"lineage {owner}: a member id is malformed")
    if owner not in members:
        raise RetireRefused(f"lineage {owner}: its owner is not among its members")
    return members


def _require_unlinked(worktree: Path, runs_root: Path, who: str) -> None:
    """No component below ``runs_root`` down to ``worktree`` is a link: ``rmtree`` follows
    a linked parent into a tree that was never archived. ``runs_root`` itself may be one."""
    path = runs_root
    for part in worktree.relative_to(runs_root).parts:
        path /= part
        if path.is_symlink():
            raise RetireRefused(f"{who}: {path} is a symbolic link; nothing deleted")
    if _real(worktree) != str(Path(_real(runs_root)) / worktree.relative_to(runs_root)):
        raise RetireRefused(
            f"{who}: {worktree} resolves to {_real(worktree)}, outside its own run "
            "directory; nothing deleted"
        )


def _require_owner_worktree(journal: Journal, registry: Registry) -> None:
    """The only worktree a cleanup removes is the owner's own: ``<runs>/<owner>/wt``."""
    expected = registry.runs_root / journal.owner / "wt"
    if journal.worktree != expected:
        raise RetireRefused(
            f"lineage {journal.owner}: the journal's worktree is {journal.worktree}, "
            f"expected {expected}"
        )
    _require_unlinked(expected, registry.runs_root, f"lineage {journal.owner}")


def inspect(
    *,
    state: Path,
    registry: Registry,
    owner: str,
    keep_branch: bool,
    environ: Mapping[str, str],
    now: str,
) -> Journal:
    """Every check of spec §3.3, then the journal to publish. Read-only git only."""
    if not RUN_ID_PATTERN.fullmatch(owner):
        raise Unknown(f"not a lineage owner: {owner!r}")
    current = lineages.load(state, owner)
    members = _members_of(current, owner)
    if current.branch != f"ha/{owner}":
        raise RetireRefused(f"lineage {owner}: branch {current.branch!r}, expected ha/{owner}")
    try:
        entry = registry.resolve(owner)
    except RegistryError as exc:
        raise RetireRefused(f"lineage {owner}: its owner has no registry entry ({exc})") from None
    if entry.lineage != owner:
        raise RetireRefused(f"lineage {owner}: the owner's entry names lineage {entry.lineage}")
    if entry.repository is None or _real(entry.repository) != _real(current.repository):
        raise RetireRefused(
            f"lineage {owner}: repository {current.repository}, the owner's entry says "
            f"{entry.repository}"
        )
    expected = entry.run_dir / "wt"
    if current.worktree != expected:
        raise RetireRefused(f"lineage {owner}: worktree {current.worktree}, expected {expected}")
    if not _inside(entry.run_dir, registry.runs_root):
        raise RetireRefused(f"lineage {owner}: {entry.run_dir} is outside the runs directory")
    for path in (entry.run_dir, expected):
        if path.is_symlink():
            raise RetireRefused(f"{path} is a symbolic link: nothing cleaned")

    files: list[Lifted] = []
    intent = state / UNCONFINED_INTENT
    if os.path.lexists(intent):
        try:
            named = read(intent).get("run_id")
        except Unknown as exc:
            raise RetireRefused(f"{intent} is unreadable ({exc}): inspect it first") from None
        if named not in members:
            raise RetireRefused(
                f"the unconfined intent names run {named}, not a member of lineage {owner}"
            )
        files.append(Lifted(UNCONFINED_INTENT, sha256_of(intent)))
    repo_q = _quarantine_of(
        quarantine.quarantine_path(state, "repository", current.common_dir), members
    )
    operator_q = _quarantine_of(quarantine.quarantine_path(state, "operator", None), members)
    if (
        current.compromised is None
        and current.pending is None
        and repo_q is None
        and operator_q is None
    ):
        raise RetireRefused(
            f"lineage {owner} is healthy: nothing to retire; ha clean {owner} removes its "
            "worktree and keeps the branch"
        )
    for member in members:
        record = state / "runs" / f"{member}.json"
        if os.path.lexists(record):
            files.append(Lifted(f"runs/{member}.json", sha256_of(record)))
    files.append(Lifted(f"lineages/{owner}.json", sha256_of(lineages.lineage_path(state, owner))))
    files.extend(q for q in (repo_q, operator_q) if q is not None)
    files.append(Lifted(f"lineages/{owner}.lock", None))

    registered, others = worktrees(
        current.repository, current.branch, expected, state=state, environ=environ
    )
    if others:
        raise RetireRefused(
            f"{current.branch} is checked out in {', '.join(others)}: deleting it would "
            "leave that worktree on an unborn branch"
        )
    if os.path.lexists(expected) and not registered:
        raise RetireRefused(f"{expected} exists but is not a worktree of {current.repository}")
    return Journal(
        owner=owner,
        members=members,
        repository=current.repository,
        common_dir=current.common_dir,
        worktree=expected,
        worktree_registered=registered,
        branch=current.branch,
        tip=branch_tip(current.repository, current.branch, state=state, environ=environ),
        base=current.base,
        keep_branch=keep_branch,
        lifted_at=now,
        files=tuple(files),
        archive=None,
        tree=None,
        bundle=None,
        steps=dict.fromkeys(STEPS, False),
        completed=False,
    )


def _mark(journal: Journal, step: str, **changes: object) -> Journal:
    return replace(journal, steps={**journal.steps, step: True}, **changes)  # type: ignore[arg-type]


def _write_atomically(target: Path, write: Callable[[Path], None]) -> str:
    """``write`` a temporary file beside ``target``, fsync it, rename it; its sha256."""
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.unlink(missing_ok=True)
    # A killed ``git bundle create`` leaves its lock, and the next one would fail on it.
    temporary.with_name(f"{temporary.name}.lock").unlink(missing_ok=True)
    try:
        write(temporary)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        _fsync_dir(target.parent)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return sha256_of(target)


def _is_git(name: str) -> bool:
    return name == "wt/.git" or name.startswith("wt/.git/")


def _without_git(member: tarfile.TarInfo) -> tarfile.TarInfo | None:
    return None if _is_git(member.name) else member


def _tree_digest(root: Path) -> str:
    """A sha256 over what the archive holds of ``root``: per entry its path, type, mode
    and size, the sha256 of a regular file, the target of a link. Same exclusions as the
    archive (``wt/.git``, sockets); no timestamp, no owner, no git."""
    digest = hashlib.sha256()

    def visit(path: Path, name: str) -> None:
        if _is_git(name):
            return
        info = path.lstat()
        mode = info.st_mode
        if stat.S_ISSOCK(mode):
            return
        kind, size, content = "o", 0, ""
        if stat.S_ISDIR(mode):
            kind = "d"
        elif stat.S_ISLNK(mode):
            kind, content = "l", os.readlink(path)
        elif stat.S_ISREG(mode):
            kind, size, content = "f", info.st_size, sha256_of(path)
        fields = (name, kind, str(stat.S_IMODE(mode)), str(size), content)
        digest.update(b"\0".join(os.fsencode(field) for field in fields) + b"\0")
        if kind == "d":
            for child in sorted(os.listdir(path)):
                visit(path / child, f"{name}/{child}")

    visit(root, "wt")
    return digest.hexdigest()


def _intact(saved: Saved | None, state: Path) -> bool:
    """Whether ``saved`` names a file that exists with its recorded sha256."""
    if saved is None:
        return False
    path = state / saved.path
    return path.is_file() and sha256_of(path) == saved.sha256


def _unsaved_revisions(journal: Journal, tip: str) -> list[str] | None:
    """What ``git bundle create`` must pack to save the branch's commits, or ``None``
    when the branch is still at its base."""
    ref = f"refs/heads/{journal.branch}"
    if journal.base is not None:
        return None if tip == journal.base else [f"{journal.base}..{ref}"]
    # No base was ever resolved: the whole branch is saved, whatever other refs hold now
    # or later, so the recorded tip survives the deletion.
    return [ref]


def _save_bundle(
    journal: Journal,
    tip: str,
    revisions: Sequence[str],
    directory: Path,
    *,
    state: Path,
    environ: Mapping[str, str],
) -> Saved:
    target = directory / "commits.bundle"
    ref = f"refs/heads/{journal.branch}"

    def write_bundle(path: Path) -> None:
        result = git(
            journal.repository, ["bundle", "create", str(path), *revisions], environ, state=state
        )
        if result.returncode != 0:
            raise RetireRefused(f"save_residue: git bundle failed: {result.stderr.strip()}")
        # The ref may have moved since the journal recorded its tip: what is deleted
        # later is that tip, so the bundle must hold exactly it.
        heads = git(journal.repository, ["bundle", "list-heads", str(path)], environ, state=state)
        if f"{tip} {ref}" not in heads.stdout.splitlines():
            raise RetireRefused(
                f"save_residue: expected the bundle to hold {ref} at {tip}; "
                f"it lists {heads.stdout.strip() or 'nothing'}"
            )

    return Saved(str(target.relative_to(state)), _write_atomically(target, write_bundle))


def save_residue(journal: Journal, *, state: Path, environ: Mapping[str, str]) -> Journal:
    """Step 1: the worktree as a tar.gz archive (no git in it), the commits as a bundle.

    A residue the journal already records, intact, is kept: a resume after a
    half-finished removal must not replace the complete archive with a partial one."""
    directory = ensure_dir(residue_dir(state, journal.owner))
    archive, tree = journal.archive, journal.tree
    if archive is not None and not _intact(archive, state):
        if not os.path.lexists(journal.worktree):
            raise RetireRefused(
                f"save_residue: expected the archive {archive.path} with sha256 "
                f"{archive.sha256}; it is missing or altered and the worktree is gone"
            )
        archive = tree = None
    if archive is None and os.path.lexists(journal.worktree):
        if journal.worktree.is_symlink():
            raise RetireRefused(f"save_residue: {journal.worktree} is a symbolic link")
        target = directory / "worktree.tar.gz"
        tree = _tree_digest(journal.worktree)

        def write_archive(path: Path) -> None:
            with tarfile.open(path, "w:gz", dereference=False) as stream:
                stream.add(journal.worktree, arcname="wt", filter=_without_git)
            if _tree_digest(journal.worktree) != tree:
                raise RetireRefused(
                    f"save_residue: {journal.worktree} changed while it was archived; "
                    "nothing deleted"
                )

        archive = Saved(str(target.relative_to(state)), _write_atomically(target, write_archive))
    bundle = journal.bundle if _intact(journal.bundle, state) else None
    tip = journal.tip
    if bundle is None and tip is not None:
        revisions = _unsaved_revisions(journal, tip)
        if revisions is not None:
            bundle = _save_bundle(journal, tip, revisions, directory, state=state, environ=environ)
    return _mark(journal, "save_residue", archive=archive, tree=tree, bundle=bundle)


def remove_worktree(journal: Journal, *, state: Path, environ: Mapping[str, str]) -> Journal:
    """Step 2: ``rmtree`` the worktree (links are not followed), then prune git's entry.

    Nothing is deleted that was not saved first."""
    present = os.path.lexists(journal.worktree)
    registered, _ = worktrees(
        journal.repository, journal.branch, journal.worktree, state=state, environ=environ
    )
    if not present and not registered:
        return _mark(journal, "remove_worktree")
    if present:
        if journal.worktree.is_symlink():
            raise RetireRefused(f"remove_worktree: {journal.worktree} is a symbolic link")
        _require_unlinked(journal.worktree, journal.worktree.parent.parent, "remove_worktree")
        if not journal.steps["save_residue"]:
            raise RetireRefused(
                "remove_worktree: expected save_residue done before deleting "
                f"{journal.worktree}; it is not"
            )
        if journal.archive is None:
            raise RetireRefused(
                f"remove_worktree: {journal.worktree} exists but the journal records no "
                "archive of it (it appeared after save_residue); nothing deleted"
            )
        if not _intact(journal.archive, state):
            raise RetireRefused(
                f"remove_worktree: expected the archive {journal.archive.path} with sha256 "
                f"{journal.archive.sha256}; it is missing or altered"
            )
        if _tree_digest(journal.worktree) != journal.tree:
            raise RetireRefused(
                f"remove_worktree: {journal.worktree} changed since its archive; nothing deleted"
            )
        if not registered:
            raise RetireRefused(
                f"remove_worktree: {journal.worktree} is not a registered worktree of "
                f"{journal.repository}; nothing deleted"
            )
        shutil.rmtree(journal.worktree)
    result = git(journal.repository, ["worktree", "prune", "--expire=now"], environ, state=state)
    if result.returncode != 0:
        raise RetireRefused(f"remove_worktree: git worktree prune failed: {result.stderr.strip()}")
    registered, _ = worktrees(
        journal.repository, journal.branch, journal.worktree, state=state, environ=environ
    )
    if registered:
        raise RetireRefused(
            f"remove_worktree: expected {journal.worktree} unregistered after prune; "
            "git still lists it"
        )
    return _mark(journal, "remove_worktree")


def delete_branch(journal: Journal, *, state: Path, environ: Mapping[str, str]) -> Journal:
    """Step 3: compare-and-delete ``refs/heads/ha/<owner>`` at the recorded tip.

    Nothing is deleted whose commits were not saved first."""
    if journal.keep_branch or journal.tip is None:
        return _mark(journal, "delete_branch")
    found = branch_tip(journal.repository, journal.branch, state=state, environ=environ)
    if found is None:
        return _mark(journal, "delete_branch")  # deleted before a crash: done
    if found != journal.tip:
        raise RetireRefused(
            f"delete_branch: {journal.branch} expected at {journal.tip}, found {found}: "
            "it was replaced since the journal was written; it is kept"
        )
    if not journal.steps["save_residue"]:
        raise RetireRefused(
            f"delete_branch: expected save_residue done before deleting {journal.branch}; it is not"
        )
    if journal.bundle is not None and not _intact(journal.bundle, state):
        raise RetireRefused(
            f"delete_branch: expected the bundle {journal.bundle.path} with sha256 "
            f"{journal.bundle.sha256}; it is missing or altered"
        )
    _, others = worktrees(
        journal.repository, journal.branch, journal.worktree, state=state, environ=environ
    )
    if others:
        raise RetireRefused(
            f"delete_branch: {journal.branch} is checked out in {', '.join(others)}"
        )
    result = git(
        journal.repository,
        ["update-ref", "-d", f"refs/heads/{journal.branch}", journal.tip],
        environ,
        state=state,
    )
    if result.returncode != 0:
        after = branch_tip(journal.repository, journal.branch, state=state, environ=environ)
        if after is not None:
            raise RetireRefused(
                f"delete_branch: expected {journal.branch} at {journal.tip}, found {after}"
            )
    return _mark(journal, "delete_branch")


def _identity(path: Path, entry: Lifted) -> bool:
    """A regular file (never a link) with the recorded sha256; a lock needs only to exist."""
    if not path.is_file() or path.is_symlink():
        return False
    return entry.sha256 is None or sha256_of(path) == entry.sha256


def lift_files(journal: Journal, *, state: Path) -> Journal:
    """Step 4: rename each file to ``<name>.lifted-<lifted_at>``, in the journal's order."""
    for entry in journal.files:
        source = state / entry.path
        target = state / f"{entry.path}.lifted-{journal.lifted_at}"
        if not os.path.lexists(source) and _identity(target, entry):
            continue
        if _identity(source, entry) and not os.path.lexists(target):
            os.rename(source, target)
            _fsync_dir(source.parent)
            continue
        raise RetireRefused(
            f"lift_files: expected {source} with sha256 {entry.sha256} and no {target.name}; "
            f"found source {'present' if os.path.lexists(source) else 'absent'}, "
            f"target {'present' if os.path.lexists(target) else 'absent'}"
        )
    return _mark(journal, "lift_files", completed=True)


def _crash_after(step: str) -> None:
    """A test hook: monkeypatched to raise after a step; does nothing in production."""


def _run_step(name: str, journal: Journal, *, state: Path, environ: Mapping[str, str]) -> Journal:
    if name == "save_residue":
        return save_residue(journal, state=state, environ=environ)
    if name == "remove_worktree":
        return remove_worktree(journal, state=state, environ=environ)
    if name == "delete_branch":
        return delete_branch(journal, state=state, environ=environ)
    return lift_files(journal, state=state)


def retire(
    run_id: str,
    *,
    owner: str,
    state: Path,
    registry: Registry,
    environ: Mapping[str, str],
    say: Callable[[str], None],
    keep_branch: bool,
    now: str,
) -> int:
    """Spec §3.2-3.5 under the registry lock, then the lineage lock, both exclusive.

    The caller holds the run's lifecycle lock and the global admission exclusive.
    Exit 0 when the lineage is retired (or already was), 1 when a step refused: the
    journal keeps what is done, and the next ``ha clean --force`` resumes it.
    """
    path = journal_path(state, owner)
    with ExitStack() as held_locks:
        held_locks.enter_context(
            locks.held(
                lineages.registry_lock(state),
                rank=locks.Rank.LINEAGE_REGISTRY,
                exclusive=True,
                wait=locks.LOCK_WAIT_SECONDS,
                what="the lineage registry lock",
            )
        )
        held_locks.enter_context(
            locks.held(
                lineages.lineage_lock(state, owner),
                rank=locks.Rank.LINEAGE,
                exclusive=True,
                wait=locks.LOCK_WAIT_SECONDS,
                what=f"the lineage lock of {owner}",
                key=owner,
            )
        )
        step = "inspection"
        # What a failure leaves behind: nothing before the journal exists, else the journal.
        retry = f"the journal {path} keeps what is done; retry ha clean --force {run_id}"
        left = "nothing cleaned"
        try:
            try:
                journal = load_journal(state, owner)
            except Unknown as exc:
                say(f"{exc}: the journal {path} is unreadable; recover it by hand")
                return 1
            if journal is not None:
                left = retry
                _require_owner_worktree(journal, registry)
                members = journal.members
            else:
                members = _members_of(lineages.load(state, owner), owner)
            if run_id not in members:
                kept = (
                    f"the journal {path} keeps what is done"
                    if journal is not None
                    else "nothing cleaned"
                )
                say(
                    f"{run_id} is not a member of lineage {owner}: ha clean --force {owner} "
                    f"retires it; {kept}"
                )
                return 1
            for member in members:
                if member != run_id and not locks.is_free(registry.lifecycle_lock(member)):
                    say(f"{member} is active: {left}")
                    return 1
            if journal is None:
                journal = inspect(
                    state=state,
                    registry=registry,
                    owner=owner,
                    keep_branch=keep_branch,
                    environ=environ,
                    now=now,
                )
                _require_owner_worktree(journal, registry)
                create_once(path, to_document(journal))
                left = retry
                say(f"journal: {path}")
            elif journal.completed:
                say(f"lineage {owner} already cleaned (journal {path})")
                return 0
            elif journal.keep_branch != keep_branch:
                flag = " --keep-branch" if journal.keep_branch else ""
                say(
                    f"the journal {path} was started with keep_branch={journal.keep_branch}; "
                    f"retry ha clean --force {run_id}{flag}"
                )
                return 1
            for step in STEPS:
                if journal.steps[step]:
                    continue
                journal = _run_step(step, journal, state=state, environ=environ)
                publish(path, to_document(journal))
                say(f"{step}: done")
                _crash_after(step)
        except RetireRefused as exc:
            say(f"{exc}; {left}")
            return 1
        except Unknown as exc:
            say(f"{exc}; {left}" if left == retry else f"{exc}: recover it by hand; {left}")
            return 1
        except (GitTampered, subprocess.TimeoutExpired, OSError) as exc:
            say(f"{step}: {type(exc).__name__}: {exc}; {left}")
            return 1
    residue = [s.path for s in (journal.archive, journal.bundle) if s is not None]
    say(
        f"lineage {owner} retired; residue: {', '.join(residue) or 'none'} under {state}; "
        f"run directories kept: {', '.join(str(registry.runs_root / m) for m in journal.members)}"
    )
    return 0
