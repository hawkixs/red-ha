"""The vendor rule: who wrote each commit of a range, and whether a reviewer shares a vendor
with them (spec 0.5.0 §3.8.4 steps 4-5, §3.8.6)."""

from __future__ import annotations

import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from headless_agents import provenance
from headless_agents.model_vendor import LinkRecord, load_table, shipped_table
from headless_agents.state import Unknown, publish
from headless_agents.vendor import (
    AttributedCommit,
    LegacyVendorCheck,
    PanelMember,
    VendorCheck,
    VendorRefused,
    attribute,
    check_from_document,
    check_independence,
)

SHA_1 = "1" * 40
SHA_2 = "2" * 40
SHA_3 = "3" * 40
RUN = "20260926T100000-aaaaaaaa"
OTHER = "20260926T110000-bbbbbbbb"


TABLE = shipped_table()


def _link(provider: str, model: str = "", *switches: str) -> LinkRecord:
    return LinkRecord(provider, model, tuple(switches))


def _record(state: Path, sha: str, *links: LinkRecord, made_by: str = "engine") -> None:
    provenance.record(
        state,
        sha,
        run_id=RUN,
        lineage=RUN,
        made_by=made_by,  # type: ignore[arg-type]
        providers=[link.provider for link in links],
        links=links,
    )


def _panel(*members: tuple[str, str, LinkRecord]) -> list[PanelMember]:
    return [PanelMember(slot, role, (link,)) for slot, role, link in members]


def _writers(state: Path, *providers: list[str]) -> None:
    publish(
        state / "unconfined-writers.json",
        {
            "writers": [
                {
                    "run_id": OTHER,
                    "repository": "/r",
                    "providers": p,
                    "links": [
                        {"provider": provider, "model": "", "switches": []} for provider in p
                    ],
                }
                for p in providers
            ]
        },
    )


def test_a_recorded_commit_takes_its_recorded_providers(tmp_path: Path) -> None:
    _record(tmp_path, SHA_1, _link("opencode", "opencode-go/glm-5"), _link("codex"))
    (commit,) = attribute(tmp_path, [(SHA_1, "chore(ha): x implement via opencode/m")])
    assert commit == AttributedCommit(
        sha=SHA_1,
        run_id=RUN,
        made_by="engine",
        providers=("opencode", "codex"),
        links=(_link("opencode", "opencode-go/glm-5"), _link("codex")),
    )


def test_a_commit_without_provenance_is_hand_written_when_no_unconfined_write_ran(
    tmp_path: Path,
) -> None:
    (commit,) = attribute(tmp_path, [(SHA_2, "fix: a typo")])
    assert commit == AttributedCommit(
        sha=SHA_2, run_id=None, made_by="hand", providers=(), links=()
    )


def test_a_commit_without_provenance_is_attributed_to_every_unconfined_writer(
    tmp_path: Path,
) -> None:
    """§3.8.4 step 4: once an unconfined write ran, nothing is presumed hand-written."""
    _writers(tmp_path, ["claude"], ["opencode", "claude"])
    (commit,) = attribute(tmp_path, [(SHA_2, "fix: a typo")])
    assert commit == AttributedCommit(
        sha=SHA_2,
        run_id=None,
        made_by="unknown",
        providers=("claude", "opencode"),
        links=(_link("claude"), _link("opencode")),
    )


@pytest.mark.parametrize("subject", ["chore(ha): 2026 implement via codex/m", "chore(ha):"])
def test_a_chore_ha_commit_without_provenance_refuses(tmp_path: Path, subject: str) -> None:
    """A 0.4.0 write run or a lost state directory: no legacy fallback (§3.8.4 step 4)."""
    with pytest.raises(VendorRefused, match=rf"{SHA_3[:12]}.*chore\(ha\).*no provenance"):
        attribute(tmp_path, [(SHA_3, subject)])


def test_an_unreadable_provenance_record_refuses(tmp_path: Path) -> None:
    (tmp_path / "provenance").mkdir()
    (tmp_path / "provenance" / f"{SHA_1}.json").write_text("{not json")
    with pytest.raises(VendorRefused, match="unknown"):
        attribute(tmp_path, [(SHA_1, "anything")])


@pytest.mark.parametrize(
    "change",
    [
        {"providers": "codex"},
        {"providers": []},
        {"providers": ["codex", ""]},
        {"providers": ["codex", 7]},
        {"providers": None},
        {"made_by": "hand"},
        {"made_by": None},
        {"made_by": []},
        {"made_by": {}},
        {"run_id": None},
        {"run_id": ""},
        {"run_id": 7},
    ],
)
def test_a_malformed_provenance_record_refuses(tmp_path: Path, change: dict[str, object]) -> None:
    """A readable record that is not one ``ha`` writes is unknown, never empty (§3.8.1)."""
    publish(
        tmp_path / "provenance" / f"{SHA_1}.json",
        {
            "sha": SHA_1,
            "run_id": RUN,
            "lineage": RUN,
            "made_by": "engine",
            "providers": ["codex"],
            **change,
        },
    )
    with pytest.raises(VendorRefused, match="malformed"):
        attribute(tmp_path, [(SHA_1, "chore(ha): x implement via codex/m")])


def test_an_unreadable_writers_list_refuses(tmp_path: Path) -> None:
    """Unknown is never empty (§3.8.1): an unreadable list cannot presume a hand."""
    (tmp_path / "unconfined-writers.json").write_text("{not json")
    with pytest.raises(VendorRefused, match="unconfined-writers.json"):
        attribute(tmp_path, [(SHA_2, "fix: a typo")])


@pytest.mark.parametrize(
    "document",
    [
        {"writers": [{"run_id": OTHER, "repository": "/r", "providers": []}]},
        {"writers": [{"run_id": OTHER, "repository": "/r", "providers": ["claude", ""]}]},
        {"writers": [{"repository": "/r", "providers": ["claude"]}]},
        {"writers": [{"run_id": "", "repository": "/r", "providers": ["claude"]}]},
        {"writers": ["claude"]},
        {"writers": {"claude": 1}},
        {},
    ],
)
def test_a_malformed_writers_list_refuses(tmp_path: Path, document: dict[str, object]) -> None:
    """Codex review of PR A: a writer with no provider made the union empty, and a commit
    without provenance was then presumed hand-written although an unconfined write ran."""
    publish(tmp_path / "unconfined-writers.json", document)
    with pytest.raises(VendorRefused, match="malformed"):
        attribute(tmp_path, [(SHA_2, "fix: a typo")])


def test_a_writers_list_rebuilt_after_it_was_unreadable_refuses(tmp_path: Path) -> None:
    """A write that found the list unreadable starts a new one and marks it: the writers it
    lost are unknown, so no commit without provenance can be presumed hand-written."""
    publish(
        tmp_path / "unconfined-writers.json",
        {
            "writers": [{"run_id": OTHER, "repository": "/r", "providers": ["claude"]}],
            "unreadable_before": True,
        },
    )
    with pytest.raises(VendorRefused, match="unreadable"):
        attribute(tmp_path, [(SHA_2, "fix: a typo")])


def test_independent_reviewers_pass_and_the_check_says_who_wrote_what(tmp_path: Path) -> None:
    _record(tmp_path, SHA_1, _link("opencode", "opencode-go/glm-5"), _link("codex"))
    commits = attribute(tmp_path, [(SHA_1, "chore(ha): x"), (SHA_2, "docs: by hand")])
    check = check_independence(
        commits,
        _panel(
            ("review", "reviewer-agy", _link("agy", "gemini-x")),
            ("review", "reviewer-claude", _link("claude")),
        ),
        TABLE,
    )
    assert check.authors == ("openai", "zhipu")
    assert check.commits[1].links == ()
    assert check_from_document(check.to_document(), where="t") == check


def test_a_reviewer_sharing_any_link_with_an_author_is_refused_naming_all_three(
    tmp_path: Path,
) -> None:
    """Every link of the reviewer's chain counts (§3.8.4 step 5)."""
    _record(tmp_path, SHA_1, _link("opencode", "opencode-go/glm-5"), _link("codex"))
    commits = attribute(tmp_path, [(SHA_1, "chore(ha): x")])
    with pytest.raises(VendorRefused, match=rf"reviewer-pair.*codex.*{SHA_1[:12]}"):
        check_independence(
            commits,
            [PanelMember("review", "reviewer-pair", (_link("agy", "gemini-x"), _link("codex")))],
            TABLE,
        )


def test_a_hand_written_commit_constrains_nothing(tmp_path: Path) -> None:
    commits = attribute(tmp_path, [(SHA_2, "docs: by hand")])
    check = check_independence(commits, _panel(("review", "reviewer-codex", _link("codex"))), TABLE)
    assert check.authors == ()


def test_a_check_round_trips_through_its_document() -> None:
    check = LegacyVendorCheck(
        commits=(
            AttributedCommit(sha=SHA_1, run_id=RUN, made_by="agent", providers=("claude",)),
            AttributedCommit(sha=SHA_2, run_id=None, made_by="hand", providers=()),
            AttributedCommit(sha=SHA_3, run_id=None, made_by="unknown", providers=("agy",)),
        ),
        authors=("agy", "claude"),
        reviewers={"r": ("codex",)},
    )
    assert LegacyVendorCheck.from_document(check.to_document(), where="x") == check


def _commit(**change: object) -> dict[str, object]:
    return {"sha": SHA_1, "run_id": RUN, "made_by": "engine", "providers": ["claude"], **change}


@pytest.mark.parametrize(
    ("commits", "authors", "reviewers"),
    [
        ([_commit()], [], {"r": ["codex"]}),
        ([_commit()], ["claude", "agy"], {"r": ["codex"]}),
        ([_commit(made_by="hand", run_id=None)], ["claude"], {"r": ["codex"]}),
        ([_commit(made_by="hand", providers=[])], [], {"r": ["codex"]}),
        ([_commit(run_id=None)], ["claude"], {"r": ["codex"]}),
        ([_commit(providers=[])], [], {"r": ["codex"]}),
        ([_commit(made_by="unknown", run_id=None, providers=[])], [], {"r": ["codex"]}),
        ([_commit()], ["claude"], {"r": ["codex", "claude"]}),
        ([_commit()], ["claude"], {}),
        ([_commit()], ["claude"], {"r": []}),
    ],
)
def test_a_check_ha_could_not_have_made_is_unknown(
    commits: list[object], authors: list[str], reviewers: dict[str, object]
) -> None:
    """Codex review of PR A, round 2: every field well-typed is not enough -- the authors are
    the union of the commits' providers, each commit's fields are an attribution ``ha``
    makes, and every reviewer is independent of them."""
    document = {"commits": commits, "authors": authors, "reviewers": reviewers}
    with pytest.raises(Unknown, match="where"):
        LegacyVendorCheck.from_document(document, where="where")


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"commits": "x", "authors": [], "reviewers": {}},
        {"commits": [{"sha": "nope"}], "authors": [], "reviewers": {}},
        {"commits": [], "authors": [1], "reviewers": {}},
        {"commits": [], "authors": [], "reviewers": {"r": "codex"}},
        {
            "commits": [{"sha": "1" * 40, "run_id": None, "made_by": [], "providers": []}],
            "authors": [],
            "reviewers": {},
        },
    ],
)
def test_a_malformed_check_is_unknown(document: dict[str, object]) -> None:
    with pytest.raises(Unknown, match="where"):
        LegacyVendorCheck.from_document(document, where="where")


def test_same_lab_through_another_provider_is_refused(tmp_path: Path) -> None:
    _record(tmp_path, SHA_1, _link("vibe", "mistral-large-4"))
    commits = attribute(tmp_path, [(SHA_1, "feat: x")])
    with pytest.raises(VendorRefused, match="mistral"):
        check_independence(
            commits, _panel(("review", "r", _link("mistral", "mistral-small-4"))), TABLE
        )


@pytest.mark.parametrize(
    ("author", "reviewer"),
    [
        (_link("claude", "claude-x"), _link("openrouter", "anthropic/claude-y")),
        (_link("codex", "gpt-6"), _link("opencode", "openrouter/openai/gpt-6")),
        (
            _link("opencode", "openrouter/deepseek/deepseek-v4-pro"),
            _link("opencode", "opencode-go/deepseek-v4-pro"),
        ),
    ],
)
def test_cross_router_pairs_of_one_lab_are_refused(
    tmp_path: Path, author: LinkRecord, reviewer: LinkRecord
) -> None:
    _record(tmp_path, SHA_1, author)
    with pytest.raises(VendorRefused):
        check_independence(
            attribute(tmp_path, [(SHA_1, "x")]), _panel(("review", "r", reviewer)), TABLE
        )


def test_same_provider_different_labs_passes(tmp_path: Path) -> None:
    _record(tmp_path, SHA_1, _link("opencode", "opencode-go/deepseek-v4-pro"))
    check = check_independence(
        attribute(tmp_path, [(SHA_1, "x")]),
        _panel(
            ("review", "r", _link("opencode", "anthropic/claude-x")),
            ("judge", "j", _link("agy", "gemini-3.8-flash-high")),
        ),
        TABLE,
    )
    assert check.authors == ("deepseek",)
    assert check_from_document(check.to_document(), where="t") == check


def test_the_judge_is_under_the_rule(tmp_path: Path) -> None:
    _record(tmp_path, SHA_1, _link("codex", "gpt-6"))
    with pytest.raises(VendorRefused, match="judge"):
        check_independence(
            attribute(tmp_path, [(SHA_1, "x")]),
            _panel(
                ("review", "r", _link("agy", "gemini-x")), ("judge", "j", _link("codex", "gpt-6"))
            ),
            TABLE,
        )


def test_an_unknown_panel_vendor_is_refused_with_the_line_to_add(tmp_path: Path) -> None:
    _record(tmp_path, SHA_1, _link("codex", "gpt-6"))
    with pytest.raises(VendorRefused, match=r"vendors\.toml"):
        check_independence(
            attribute(tmp_path, [(SHA_1, "x")]),
            _panel(("review", "r", _link("openai-compat", "my-model"))),
            TABLE,
        )
    with pytest.raises(VendorRefused) as error:
        check_independence(
            attribute(tmp_path, [(SHA_1, "x")]),
            _panel(("review", "r", _link("openai-compat", "my-model"))),
            TABLE,
        )
    assert 'provider = "openai-compat"' in str(error.value)
    assert 'model = "my-model"' in str(error.value)


def test_unknown_panel_hint_is_valid_toml_and_loadable(tmp_path: Path) -> None:
    _record(tmp_path, SHA_1, _link("claude", "claude-x"))
    with pytest.raises(VendorRefused) as error:
        check_independence(
            attribute(tmp_path, [(SHA_1, "x")]),
            _panel(("review", "r", _link("openai-compat", "my-model"))),
            TABLE,
        )
    suggestion = str(error.value).split("\n", 1)[1].replace("<lab>", "acme")
    parsed = tomllib.loads(suggestion)
    path = tmp_path / "vendors.toml"
    path.write_text(suggestion, encoding="utf-8")
    assert load_table(path).resolve("openai-compat", "my-model") == parsed["pair"][0]["vendor"]


def test_unknown_panel_hint_without_model_suggests_no_pair(tmp_path: Path) -> None:
    _record(tmp_path, SHA_1, _link("claude", "claude-x"))
    with pytest.raises(VendorRefused, match="no model is configured") as error:
        check_independence(
            attribute(tmp_path, [(SHA_1, "x")]),
            _panel(("review", "r", _link("openai-compat", ""))),
            TABLE,
        )
    assert "[[pair]]" not in str(error.value)


def test_legacy_unconfined_writers_get_a_dedicated_refusal(tmp_path: Path) -> None:
    publish(
        tmp_path / "unconfined-writers.json",
        {
            "writers": [
                {"run_id": "a", "providers": ["claude"]},
                {"run_id": "b", "providers": ["codex"]},
                {
                    "run_id": "c",
                    "providers": ["agy"],
                    "links": [{"provider": "agy", "model": "gemini-x", "switches": []}],
                },
            ]
        },
    )
    commits = attribute(tmp_path, [(SHA_1, "handwritten")])
    with pytest.raises(VendorRefused) as error:
        check_independence(commits, _panel(("review", "r", _link("agy", "gemini-x"))), TABLE)
    message = str(error.value)
    assert "unconfined-writers.json" in message
    assert "2 legacy writer" in message
    assert "Archive the list" in message


def test_unconfined_writers_with_links_do_not_get_legacy_refusal(tmp_path: Path) -> None:
    publish(
        tmp_path / "unconfined-writers.json",
        {
            "writers": [
                {
                    "run_id": "c",
                    "providers": ["codex"],
                    "links": [{"provider": "codex", "model": "gpt-6", "switches": []}],
                }
            ]
        },
    )
    checked = check_independence(
        attribute(tmp_path, [(SHA_1, "handwritten")]),
        _panel(("review", "r", _link("agy", "gemini-x"))),
        TABLE,
    )
    assert checked.authors == ("openai",)


def test_a_switched_panel_member_is_refused(tmp_path: Path) -> None:
    _record(tmp_path, SHA_1, _link("agy", "gemini-x"))
    with pytest.raises(VendorRefused, match="OPENAI_BASE_URL"):
        check_independence(
            attribute(tmp_path, [(SHA_1, "x")]),
            _panel(("review", "r", _link("codex", "gpt-6", "OPENAI_BASE_URL"))),
            TABLE,
        )


def test_an_unknown_author_refuses(tmp_path: Path) -> None:
    _record(tmp_path, SHA_1, _link("openai-compat", "m"))
    with pytest.raises(VendorRefused, match="unknown"):
        check_independence(
            attribute(tmp_path, [(SHA_1, "x")]),
            _panel(("review", "r", _link("agy", "gemini-x"))),
            TABLE,
        )


def test_a_legacy_record_refuses_as_predating_model_provenance(tmp_path: Path) -> None:
    publish(
        tmp_path / "provenance" / f"{SHA_1}.json",
        {"sha": SHA_1, "run_id": RUN, "lineage": RUN, "made_by": "engine", "providers": ["claude"]},
    )
    (commit,) = attribute(tmp_path, [(SHA_1, "x")])
    assert commit.links is None
    with pytest.raises(VendorRefused, match="predates model provenance"):
        check_independence((commit,), _panel(("review", "r", _link("agy", "gemini-x"))), TABLE)


@pytest.mark.parametrize(
    "links",
    [
        [],
        [{"provider": "codex", "model": "m", "switches": []}],
        [
            {"provider": "opencode", "model": "m", "switches": []},
            {"provider": "codex", "model": "m", "switches": []},
        ],
        [
            {"provider": "codex", "model": 1, "switches": []},
            {"provider": "opencode", "model": "m", "switches": []},
        ],
    ],
)
def test_present_but_incomplete_links_refuse(tmp_path: Path, links: object) -> None:
    publish(
        tmp_path / "provenance" / f"{SHA_1}.json",
        {
            "sha": SHA_1,
            "run_id": RUN,
            "lineage": RUN,
            "made_by": "engine",
            "providers": ["codex", "opencode"],
            "links": links,
        },
    )
    with pytest.raises(VendorRefused, match="malformed"):
        attribute(tmp_path, [(SHA_1, "x")])


def test_every_link_of_a_fallback_chain_is_checked(tmp_path: Path) -> None:
    _record(tmp_path, SHA_1, _link("codex", "gpt-6"))
    member = PanelMember(
        "review", "chain", (_link("agy", "gemini-x"), _link("openrouter", "openai/gpt-6"))
    )
    with pytest.raises(VendorRefused, match="openai"):
        check_independence(attribute(tmp_path, [(SHA_1, "x")]), [member], TABLE)


def test_unconfined_writers_without_links_make_unknown_authors(tmp_path: Path) -> None:
    publish(
        tmp_path / "unconfined-writers.json",
        {"writers": [{"run_id": OTHER, "repository": "/r", "providers": ["opencode"]}]},
    )
    (commit,) = attribute(tmp_path, [(SHA_1, "feat: by hand?")])
    assert commit.made_by == "unknown" and commit.links is None


def test_two_unconfined_writers_on_one_provider_keep_both_models(tmp_path: Path) -> None:
    publish(
        tmp_path / "unconfined-writers.json",
        {
            "writers": [
                {
                    "run_id": OTHER,
                    "repository": "/r",
                    "providers": ["opencode"],
                    "links": [
                        {"provider": "opencode", "model": "opencode-go/glm-5", "switches": []}
                    ],
                },
                {
                    "run_id": RUN,
                    "repository": "/r",
                    "providers": ["opencode"],
                    "links": [
                        {
                            "provider": "opencode",
                            "model": "opencode-go/deepseek-v4-pro",
                            "switches": [],
                        }
                    ],
                },
            ]
        },
    )
    (commit,) = attribute(tmp_path, [(SHA_1, "feat: x")])
    assert commit.providers == ("opencode", "opencode")
    check = check_independence((commit,), _panel(("review", "r", _link("agy", "gemini-x"))), TABLE)
    assert check.authors == ("deepseek", "zhipu")
    assert check_from_document(check.to_document(), where="t") == check


@pytest.mark.parametrize(
    "links",
    [
        None,
        [],
        [{"provider": "codex", "model": "", "switches": []}],
        [{"provider": "claude", "model": 1, "switches": []}],
    ],
)
def test_malformed_writer_links_refuse(tmp_path: Path, links: object) -> None:
    publish(
        tmp_path / "unconfined-writers.json",
        {"writers": [{"run_id": RUN, "repository": "/r", "providers": ["claude"], "links": links}]},
    )
    with pytest.raises(VendorRefused, match="malformed"):
        attribute(tmp_path, [(SHA_1, "x")])


def _valid_check() -> VendorCheck:
    return check_independence(
        (AttributedCommit(SHA_1, RUN, "engine", ("claude",), (_link("claude", "claude-x"),)),),
        _panel(("review", "r", _link("codex", "gpt-6"))),
        TABLE,
    )


INVALID_MUTATIONS = [
    lambda d: d.update(authors=["openai"]),
    lambda d: d["commits"][0]["links"][0].pop("vendor"),
    # Authors follow the mutation, so only the unknown-author clause can refuse it.
    lambda d: (d["commits"][0]["links"][0].update(vendor="unknown"), d.update(authors=["unknown"])),
    lambda d: d["panel"][0]["links"][0].update(vendor="unknown"),
    lambda d: d["panel"][0]["links"][0].update(vendor="anthropic"),
    lambda d: d.update(panel=[]),
    lambda d: d["panel"][0].update(links=[]),
    # A non-hand commit without links, its providers and authors emptied to match: refused
    # (an engine commit with no provider is no attribution ha makes, whatever its links).
    lambda d: (d["commits"][0].update(links=[], providers=[]), d.update(authors=[])),
    lambda d: d["commits"][0].update(providers=["opencode"]),
]
INVALID_IDS = [
    "authors-mismatch",
    "missing-vendor",
    "unknown-author",
    "unknown-panel",
    "panel-shares-author",
    "empty-panel",
    "empty-member-links",
    "empty-engine-links",
    "providers-mismatch",
]


def test_the_model_vendor_document_shape_is_pinned() -> None:
    assert _valid_check().to_document() == {
        "rule": "model-vendor",
        "table_digest": TABLE.digest,
        "authors": ["anthropic"],
        "commits": [
            {
                "sha": SHA_1,
                "run_id": RUN,
                "made_by": "engine",
                "providers": ["claude"],
                "links": [
                    {
                        "provider": "claude",
                        "model": "claude-x",
                        "switches": [],
                        "vendor": "anthropic",
                    }
                ],
            }
        ],
        "panel": [
            {
                "slot": "review",
                "role": "r",
                "links": [
                    {"provider": "codex", "model": "gpt-6", "switches": [], "vendor": "openai"}
                ],
            }
        ],
    }


@pytest.mark.parametrize("mutate", INVALID_MUTATIONS, ids=INVALID_IDS)
def test_an_invalid_new_shape_check_is_unknown(mutate: Callable[[dict[str, Any]], object]) -> None:
    document = _valid_check().to_document()
    mutate(document)
    with pytest.raises(Unknown):
        check_from_document(document, where="t")


def test_a_legacy_document_reads_and_round_trips() -> None:
    document = {
        "commits": [{"sha": SHA_1, "run_id": None, "made_by": "hand", "providers": []}],
        "authors": [],
        "reviewers": {"reviewer-codex": ["codex"]},
    }
    check = check_from_document(document, where="t")
    assert isinstance(check, LegacyVendorCheck)
    assert check.to_document() == document
