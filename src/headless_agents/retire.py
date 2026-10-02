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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

from . import lineage as lineages
from . import quarantine
from .gitops import git
from .runs import RUN_ID_PATTERN, Registry, RegistryError
from .state import Unknown, read, read_optional
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
    if not isinstance(members, list) or not all(
        isinstance(m, str) and RUN_ID_PATTERN.fullmatch(m) for m in members
    ):
        raise Unknown(f"{path}: members are malformed")
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
    return Journal(
        owner=owner,
        members=tuple(members),
        repository=Path(_text(document, "repository", path)),
        common_dir=Path(_text(document, "common_dir", path)),
        worktree=Path(_text(document, "worktree", path)),
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
        archive=_saved_from(document["archive"], path),
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
    members = tuple(sorted(current.members))
    if not all(RUN_ID_PATTERN.fullmatch(member) for member in members):
        raise Unknown(f"lineage {owner}: a member id is malformed")
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
        bundle=None,
        steps=dict.fromkeys(STEPS, False),
        completed=False,
    )
