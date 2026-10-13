"""The vendor rule: no reviewer or judge shares a model lab with the code's author
(spec 2026-10-13 model-vendor rule, §3.3).

Pure decisions over the state directory, no git: the review flow lists the
commits of ``<merge-base>..<head>`` and their subjects, then calls
:func:`attribute` and :func:`check_independence` under its locks.

- A commit with provenance takes its recorded links; missing links are legacy and
  cannot prove the authors' model labs. Legacy check documents remain readable.
- A ``chore(ha):`` commit without provenance -- a 0.4.0 write run, a lost state
  directory, a subject typed by hand -- refuses the review: a 0.4.0 chain that
  succeeded on its first link leaves no trace of the links it declared.
- Any other commit without provenance is hand-written (``made_by: hand``, no
  provider) only while ``unconfined-writers.json`` records no unconfined write;
  otherwise it is attributed to the union of their providers (``made_by:
  unknown``), because ``ha`` cannot see everything such a write did (§3.8.0).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, get_args

from . import provenance
from .model_vendor import UNKNOWN, LinkRecord, ResolvedLink, VendorTable
from .state import Unknown, read_optional
from .write_flow import UNCONFINED_WRITERS

#: The subject prefix of every commit ``ha`` makes (§3.6).
HA_SUBJECT: Final = "chore(ha):"
#: What a check records for each commit: the three of provenance, ``unknown`` for an
#: unconfined writer's possible commit, ``hand`` for a presumed hand-written one.
MADE_BY: Final = frozenset({"engine", "agent", "hook", "unknown", "hand"})
#: What a provenance record may say: the three of a recorded commit, and ``unknown``.
_RECORDED_MADE_BY: Final = frozenset(get_args(provenance.MadeBy))
_SHA: Final = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


class VendorRefused(Exception):  # noqa: N818 - a refusal, not a crash
    """The review cannot prove independence: exit ``2``, naming why (§3.8.4)."""


@dataclass(frozen=True)
class AttributedCommit:
    sha: str
    #: The run that recorded it; ``None`` without provenance.
    run_id: str | None
    made_by: str
    providers: tuple[str, ...]
    links: tuple[LinkRecord, ...] | None = None

    def to_document(self) -> dict[str, object]:
        return {
            "sha": self.sha,
            "run_id": self.run_id,
            "made_by": self.made_by,
            "providers": list(self.providers),
        }


@dataclass(frozen=True)
class LegacyVendorCheck:
    """What a review proved before its reviewers started (§3.8.6), ``vendor_check`` in reports."""

    commits: tuple[AttributedCommit, ...]
    #: The union of the providers attributed to the range, sorted.
    authors: tuple[str, ...]
    #: Every reviewer role with the providers of every link of its chain.
    reviewers: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def to_document(self) -> dict[str, object]:
        return {
            "commits": [commit.to_document() for commit in self.commits],
            "authors": list(self.authors),
            "reviewers": {role: list(providers) for role, providers in self.reviewers.items()},
        }

    @classmethod
    def from_document(cls, document: Mapping[str, object], *, where: str) -> LegacyVendorCheck:
        """The check ``document`` states; :class:`Unknown` when it is not one ``ha`` writes."""

        def texts(value: object) -> tuple[str, ...]:
            if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
                raise Unknown(f"{where}: vendor check is malformed")
            return tuple(value)

        raw_commits, raw_reviewers = document.get("commits"), document.get("reviewers")
        if not isinstance(raw_commits, list) or not isinstance(raw_reviewers, dict):
            raise Unknown(f"{where}: vendor check is malformed")
        commits = []
        for raw in raw_commits:
            if not isinstance(raw, dict):
                raise Unknown(f"{where}: vendor check is malformed")
            sha, run_id, made_by = raw.get("sha"), raw.get("run_id"), raw.get("made_by")
            if (
                not isinstance(sha, str)
                or not _SHA.fullmatch(sha)
                or not (run_id is None or isinstance(run_id, str))
                or not isinstance(made_by, str)
                or made_by not in MADE_BY
            ):
                raise Unknown(f"{where}: vendor check is malformed")
            commits.append(
                AttributedCommit(
                    sha=sha,
                    run_id=run_id,
                    made_by=str(made_by),
                    providers=texts(raw.get("providers")),
                )
            )
        authors = texts(document.get("authors"))
        reviewers = {str(role): texts(providers) for role, providers in raw_reviewers.items()}
        union: set[str] = set()
        for commit in commits:
            if not _attribution_ha_makes(commit):
                raise Unknown(f"{where}: vendor check holds an attribution ha never makes")
            union.update(commit.providers)
        # What check_independence writes: the sorted union, and reviewers independent of it.
        if authors != tuple(sorted(union)):
            raise Unknown(f"{where}: vendor check authors are not its commits' providers")
        if not reviewers or any(
            not providers or set(providers) & union for providers in reviewers.values()
        ):
            raise Unknown(f"{where}: vendor check reviewers are not independent of its authors")
        return cls(commits=tuple(commits), authors=authors, reviewers=reviewers)


@dataclass(frozen=True)
class PanelMember:
    slot: str
    role: str
    links: tuple[LinkRecord, ...]


@dataclass(frozen=True)
class CheckedCommit:
    sha: str
    run_id: str | None
    made_by: str
    providers: tuple[str, ...]
    links: tuple[ResolvedLink, ...]

    def to_document(self) -> dict[str, object]:
        return {
            "sha": self.sha,
            "run_id": self.run_id,
            "made_by": self.made_by,
            "providers": list(self.providers),
            "links": [link.to_document() for link in self.links],
        }


@dataclass(frozen=True)
class CheckedMember:
    slot: str
    role: str
    links: tuple[ResolvedLink, ...]

    def to_document(self) -> dict[str, object]:
        return {
            "slot": self.slot,
            "role": self.role,
            "links": [link.to_document() for link in self.links],
        }


@dataclass(frozen=True)
class VendorCheck:
    """The model labs proved independent before any reviewer or judge starts."""

    commits: tuple[CheckedCommit, ...]
    panel: tuple[CheckedMember, ...]
    authors: tuple[str, ...]
    table_digest: str

    def to_document(self) -> dict[str, object]:
        return {
            "rule": "model-vendor",
            "table_digest": self.table_digest,
            "authors": list(self.authors),
            "commits": [commit.to_document() for commit in self.commits],
            "panel": [member.to_document() for member in self.panel],
        }

    @classmethod
    def from_document(cls, document: Mapping[str, object], *, where: str) -> VendorCheck:
        """Read the recorded evidence without resolving it against today's table."""
        malformed = f"{where}: vendor check is malformed"

        def texts(raw: object) -> tuple[str, ...]:
            if not isinstance(raw, list) or not all(isinstance(v, str) and v for v in raw):
                raise Unknown(malformed)
            return tuple(raw)

        def links(raw: object) -> tuple[ResolvedLink, ...]:
            if not isinstance(raw, list):
                raise Unknown(malformed)
            found = []
            for item in raw:
                link = ResolvedLink.from_document(item)
                if link is None or link.vendor == UNKNOWN:
                    raise Unknown(malformed)
                found.append(link)
            return tuple(found)

        digest = document.get("table_digest")
        raw_commits, raw_panel = document.get("commits"), document.get("panel")
        if (
            document.get("rule") != "model-vendor"
            or not isinstance(digest, str)
            or not digest.startswith("sha256:")
            or not isinstance(raw_commits, list)
            or not isinstance(raw_panel, list)
            or not raw_panel
        ):
            raise Unknown(malformed)
        checked = []
        union: set[str] = set()
        for raw in raw_commits:
            if not isinstance(raw, dict):
                raise Unknown(malformed)
            sha, run_id, made_by = raw.get("sha"), raw.get("run_id"), raw.get("made_by")
            if (
                not isinstance(sha, str)
                or not _SHA.fullmatch(sha)
                or not (run_id is None or isinstance(run_id, str) and bool(run_id))
                or not isinstance(made_by, str)
                or made_by not in MADE_BY
            ):
                raise Unknown(malformed)
            providers = texts(raw.get("providers"))
            resolved = links(raw.get("links"))
            if (
                not _attribution_ha_makes(AttributedCommit(sha, run_id, made_by, providers))
                or (not resolved and (made_by != "hand" or providers))
                or tuple(link.provider for link in resolved) != providers
            ):
                raise Unknown(malformed)
            checked.append(CheckedCommit(sha, run_id, made_by, providers, resolved))
            union.update(link.vendor for link in resolved)
        authors = texts(document.get("authors"))
        if authors != tuple(sorted(union)):
            raise Unknown(malformed)
        members = []
        for raw in raw_panel:
            if not isinstance(raw, dict):
                raise Unknown(malformed)
            slot, role = raw.get("slot"), raw.get("role")
            if (
                not isinstance(slot, str)
                or slot not in {"review", "judge"}
                or not isinstance(role, str)
                or not role
            ):
                raise Unknown(malformed)
            resolved = links(raw.get("links"))
            if not resolved or any(link.vendor in union for link in resolved):
                raise Unknown(malformed)
            members.append(CheckedMember(slot, role, resolved))
        return cls(tuple(checked), tuple(members), authors, digest)


type AnyVendorCheck = VendorCheck | LegacyVendorCheck


def check_from_document(document: Mapping[str, object], *, where: str) -> AnyVendorCheck:
    if "rule" in document:
        return VendorCheck.from_document(document, where=where)
    return LegacyVendorCheck.from_document(document, where=where)


def _attribution_ha_makes(commit: AttributedCommit) -> bool:
    """Whether :func:`attribute` can produce ``commit``: a recorded commit names its run and
    providers; without provenance it is a hand (no provider) or an unconfined writer's."""
    if commit.run_id is not None:
        return (
            bool(commit.run_id) and commit.made_by in _RECORDED_MADE_BY and bool(commit.providers)
        )
    if commit.made_by == "hand":
        return not commit.providers
    return commit.made_by == "unknown" and bool(commit.providers)


def _unconfined_writers(state: Path) -> tuple[tuple[str, ...], tuple[LinkRecord, ...] | None]:
    """Every unconfined writing link, or legacy providers when a writer lacks links."""
    path = state / UNCONFINED_WRITERS
    try:
        document = read_optional(path)
    except Unknown as exc:
        raise VendorRefused(
            f"{path} is unknown ({exc}): a commit without provenance cannot be presumed "
            "hand-written; recover the file by hand"
        ) from None
    if document is None:
        return (), ()
    if document.get("unreadable_before"):
        # A write found the list unreadable and started a new one: the writers it lost are
        # unknown, and unknown is never empty (§3.8.1).
        raise VendorRefused(
            f"{path} was rebuilt after it was unreadable: the unconfined writers it lost are "
            "unknown, so a commit without provenance cannot be attributed; recover it by hand"
        )
    writers = document.get("writers")
    if not isinstance(writers, list):
        raise VendorRefused(f"{path} is malformed: recover it by hand")
    found: list[str] = []
    found_links: list[LinkRecord] = []
    legacy = False
    for writer in writers:
        # Every writer ha records names its run and at least one provider: a writer without
        # one would leave the union empty and presume a hand where an unconfined write ran.
        run_id = writer.get("run_id") if isinstance(writer, dict) else None
        providers = writer.get("providers") if isinstance(writer, dict) else None
        if (
            not isinstance(run_id, str)
            or not run_id
            or not isinstance(providers, list)
            or not providers
            or not all(isinstance(p, str) and p for p in providers)
        ):
            raise VendorRefused(f"{path} is malformed: recover it by hand")
        found.extend(p for p in providers if p not in found)
        assert isinstance(writer, dict)
        try:
            links = _record_links(writer, providers)
        except ValueError:
            raise VendorRefused(f"{path} is malformed: recover it by hand") from None
        if links is None:
            legacy = True
        else:
            found_links.extend(link for link in links if link not in found_links)
    if legacy:
        return tuple(found), None
    return tuple(link.provider for link in found_links), tuple(found_links)


def _record_links(
    record: Mapping[str, object], providers: Sequence[str]
) -> tuple[LinkRecord, ...] | None:
    if "links" not in record:
        return None
    raw = record["links"]
    if not isinstance(raw, list) or not raw:
        raise ValueError("links must be a non-empty list")
    links = []
    for item in raw:
        link = LinkRecord.from_document(item)
        if link is None:
            raise ValueError("a link is malformed")
        links.append(link)
    if [link.provider for link in links] != list(providers):
        raise ValueError("links do not match providers")
    return tuple(links)


def _recorded(sha: str, record: Mapping[str, object]) -> AttributedCommit:
    """The commit a provenance record states; :class:`VendorRefused` when it is not one ``ha``
    writes -- a record read as no provider would let an author's vendor review its code, and
    unknown is never empty (§3.8.1)."""
    run_id, made_by, providers = (
        record.get("run_id"),
        record.get("made_by"),
        record.get("providers"),
    )
    if (
        not isinstance(run_id, str)
        or not run_id
        or not isinstance(made_by, str)
        or made_by not in _RECORDED_MADE_BY
        or not isinstance(providers, list)
        or not providers
        or not all(isinstance(p, str) and p for p in providers)
    ):
        raise VendorRefused(
            f"the provenance record of {sha[:12]} is malformed: its authors cannot be proven; "
            "recover it by hand"
        )
    try:
        links = _record_links(record, providers)
    except ValueError as exc:
        raise VendorRefused(f"the provenance record of {sha[:12]} is malformed: {exc}") from None
    return AttributedCommit(
        sha=sha, run_id=run_id, made_by=str(made_by), providers=tuple(providers), links=links
    )


def attribute(state: Path, commits: Sequence[tuple[str, str]]) -> tuple[AttributedCommit, ...]:
    """Every ``(sha, subject)`` of the range attributed (§3.8.4 step 4), in the given order."""
    writers: tuple[tuple[str, ...], tuple[LinkRecord, ...] | None] | None = None
    attributed = []
    for sha, subject in commits:
        try:
            record = provenance.lookup(state, sha)
        except Unknown as exc:
            raise VendorRefused(f"the provenance of {sha[:12]} is unknown ({exc})") from None
        if record is not None:
            attributed.append(_recorded(sha, record))
            continue
        if subject.startswith(HA_SUBJECT):
            raise VendorRefused(
                f"commit {sha[:12]} says {HA_SUBJECT} but has no provenance (a 0.4.0 write run, "
                "a lost state directory, or a subject typed by hand): its authors cannot be proven"
            )
        if writers is None:
            writers = _unconfined_writers(state)
        providers, links = writers
        made_by = "unknown" if providers else "hand"
        attributed.append(
            AttributedCommit(
                sha=sha, run_id=None, made_by=made_by, providers=providers, links=links
            )
        )
    return tuple(attributed)


def _resolve(link: LinkRecord, table: VendorTable) -> ResolvedLink:
    return ResolvedLink(
        link.provider,
        link.model,
        link.switches,
        table.resolve(link.provider, link.model, link.switches),
    )


def _why_unknown(link: ResolvedLink) -> str:
    if link.switches:
        return (
            f"its vendor is unknown: its environment sets {', '.join(link.switches)}, "
            "which can point it at another backend"
        )
    return (
        f"its vendor is unknown; declare it in vendors.toml: "
        f'[[pair]] provider = "{link.provider}" model = "{link.model}" vendor = "<lab>"'
    )


def check_independence(
    commits: Sequence[AttributedCommit], panel: Sequence[PanelMember], table: VendorTable
) -> VendorCheck:
    """Resolve every author and panel link, refusing unknown or shared model labs."""
    if not panel or any(not member.links for member in panel):
        raise VendorRefused("the review panel has no member to check")
    checked: list[CheckedCommit] = []
    authors: set[str] = set()
    for commit in commits:
        if commit.links is None:
            raise VendorRefused(
                f"commit {commit.sha[:12]} predates model provenance: its authors' labs "
                "cannot be proven"
            )
        resolved = tuple(_resolve(link, table) for link in commit.links)
        for link in resolved:
            if link.vendor == UNKNOWN:
                raise VendorRefused(
                    f"commit {commit.sha[:12]} was written by {link.provider} {link.model or '(default)'}: "
                    + _why_unknown(link)
                )
            authors.add(link.vendor)
        checked.append(
            CheckedCommit(commit.sha, commit.run_id, commit.made_by, commit.providers, resolved)
        )
    members: list[CheckedMember] = []
    for member in panel:
        resolved = tuple(_resolve(link, table) for link in member.links)
        for link in resolved:
            label = f"{member.slot} {member.role} runs {link.provider} {link.model or '(default)'}"
            if link.vendor == UNKNOWN:
                raise VendorRefused(f"{label}: {_why_unknown(link)}")
            if link.vendor in authors:
                sha = next(
                    c.sha
                    for c in checked
                    if any(author.vendor == link.vendor for author in c.links)
                )
                raise VendorRefused(
                    f"{label}, a {link.vendor} model, and {link.vendor} wrote commit {sha[:12]} "
                    "of the reviewed range: no reviewer or judge may share a lab with the author"
                )
        members.append(CheckedMember(member.slot, member.role, resolved))
    return VendorCheck(tuple(checked), tuple(members), tuple(sorted(authors)), table.digest)


__all__ = [
    "AnyVendorCheck",
    "AttributedCommit",
    "CheckedCommit",
    "CheckedMember",
    "LegacyVendorCheck",
    "PanelMember",
    "HA_SUBJECT",
    "MADE_BY",
    "VendorCheck",
    "VendorRefused",
    "attribute",
    "check_from_document",
    "check_independence",
]
