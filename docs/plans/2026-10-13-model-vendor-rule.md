# Vendor rule by model vendor — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The review vendor rule compares the lab that trained each model (reviewers,
judge and authors), fail-closed, instead of comparing ha provider names.

**Architecture:** A new pure module `src/headless_agents/model_vendor.py` holds the
shipped table (package data `vendors.toml`), the operator's exact pairs, `resolve()` and
`backend_switches()`. Provenance and the unconfined-writers list record each link of the
writing role with its frozen model and its backend switches. `vendor.py` reads those
links, resolves vendors with the table and writes a new `VendorCheck` shape tagged
`"rule": "model-vendor"`; legacy documents keep their own class and validation. The
engine hands the whole panel (reviewers and judge) from the `SlotPlan` snapshot to
admission.

**Tech Stack:** Python 3.12.7+, uv, pytest, ruff, mypy; `tomllib`, `fnmatch`, `hashlib`,
`importlib.resources`; no new dependency.

**Spec:** `docs/specs/2026-10-13-model-vendor-rule-design.md` (approved; read §2 and §3
first — every task argues from them).

## Global Constraints

- A vendor is the model's lab; routers and hosts are never vendors (spec D1).
- `"unknown"` is never independent of anything: an unknown panel vendor refuses, an
  unknown author vendor refuses (spec D3).
- The operator file `vendors.toml` (in ha's configuration directory, beside
  `models.toml`) holds exact `(provider, model) -> vendor` pairs only, consulted only when
  the shipped table says `unknown` and never after a backend switch; a pair the shipped
  table already resolves is a configuration error (spec D2).
- Backend switches: `codex` — `OPENAI_BASE_URL`, `OPENAI_API_BASE` in the child
  environment, and `model_provider`, `model_providers.*`, `--profile`/`-p`, `--oss`,
  `--local-provider` in its argv; `claude` — `ANTHROPIC_BASE_URL`. `CLAUDE_CODE_*` is
  already removed by `engine.operator_environment` and stays so (spec D2bis).
- Provenance records links, never vendors; an absent `links` is legacy and its authors
  are `unknown` (spec D4).
- The rule applies to every slot of a review panel, judge included (spec D5).
- Strict TDD; unit tests only; no provider is called. `make ci` green at every commit.
- English everywhere; the repository is public: no private hostnames, paths or keys in
  code, tests or fixtures.

## Review Focus

- An operator `vendors.toml` that is unreadable, not TOML, or has a malformed pair: the
  review is refused (exit 2) naming the file, never a crash and never silently ignored —
  pinned in Task 1.
- A role whose chain link has no model configured anywhere (`model == ""`) for a grammar
  provider: `unknown`, refused with the `vendors.toml` hint — pinned in Task 1.
- A provenance record written by ha 0.5.4 (no `links`) inside the reviewed range: the
  review is refused with "predates model provenance", not a crash — pinned in Task 3.
- A legacy `vendor_check` document of a review run under ha 0.5.4 shown by `ha show` and
  read by `--findings`: still reads and renders — pinned in Task 4 and Task 5.
- A backend variable set to an empty string (`OPENAI_BASE_URL=`): still a switch
  (presence, not value, decides) — pinned in Task 2.

---

## File structure

| File | Responsibility |
|---|---|
| `src/headless_agents/vendors.toml` (create) | the shipped table, verbatim from spec §3.1 |
| `src/headless_agents/model_vendor.py` (create) | table loading, operator pairs, `resolve`, `backend_switches`, `LinkRecord`, `ResolvedLink` |
| `src/headless_agents/providers/codex.py` (modify) | refuse to build an argv that carries a backend switch |
| `src/headless_agents/provenance.py` (modify) | `links` in each record |
| `src/headless_agents/write_flow.py` (modify) | record links from the plan snapshot, in provenance and in unconfined writers |
| `src/headless_agents/vendor.py` (modify) | attribution with links, vendor resolution, new `VendorCheck`, `LegacyVendorCheck`, `check_from_document` |
| `src/headless_agents/reviews.py` (modify) | read checks through `check_from_document` |
| `src/headless_agents/review_flow.py`, `engine.py` (modify) | panel with judge from the slot snapshot; table loaded at planning |
| `src/headless_agents/show.py` (modify) | `vendors_line` for both shapes |
| `README.md`, `CHANGELOG.md` (modify) | the rule as it now is |

---

### Task 1: The shipped table, operator pairs and `resolve`

**Files:**
- Create: `src/headless_agents/vendors.toml`
- Create: `src/headless_agents/model_vendor.py`
- Test: `tests/unit/headless_agents/test_model_vendor.py`

**Interfaces:**
- Produces:
  - `UNKNOWN: Final = "unknown"`
  - `VENDORS_FILE_NAME: Final = "vendors.toml"`
  - `class VendorTableError(ValueError)`
  - `@dataclass(frozen=True) class VendorTable` with `resolve(provider: str, model: str, switches: Sequence[str] = ()) -> str` and property `digest -> str` (`"sha256:<hex>"`)
  - `load_table(operator_file: Path | None) -> VendorTable` (raises `VendorTableError`)
  - `shipped_table() -> VendorTable`

- [ ] **Step 1: Create `vendors.toml`** with exactly the TOML block of spec §3.1 (all
  five tables: `[providers]`, `[orgs]`, `[bare]`, `[opencode]`), comments included.

- [ ] **Step 2: Write the failing tests**

```python
"""The model-vendor table (spec 2026-10-13 model-vendor rule §3.1-§3.2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from headless_agents.model_vendor import (
    UNKNOWN,
    VendorTableError,
    load_table,
    shipped_table,
)

TABLE = shipped_table()


@pytest.mark.parametrize(
    ("provider", "model", "vendor"),
    [
        ("claude", "claude-opus-5-5", "anthropic"),
        ("claude", "", "anthropic"),
        ("codex", "gpt-6.1-sol", "openai"),
        ("vibe", "mistral-large-4", "mistral"),
        ("mistral", "mistral-large-4", "mistral"),
        ("agy", "gemini-3.8-flash-high", "google"),
        ("agy", "claude-sonnet-5-5", "anthropic"),
        ("openrouter", "z-ai/glm-5.3-flash", "zhipu"),
        ("openrouter", "moonshotai/kimi-k3", "moonshot"),
        ("openrouter", "anthropic/claude-opus-5-5", "anthropic"),
        ("openrouter", "deepseek/deepseek-v4-pro", "deepseek"),
        ("nvidia", "meta/llama-3.1-405b-instruct", "meta"),
        ("opencode", "nvidia/meta/llama-3.1-405b-instruct", "meta"),
        ("opencode", "openrouter/deepseek/deepseek-v4-pro", "deepseek"),
        ("opencode", "opencode-go/deepseek-v4-pro", "deepseek"),
        ("opencode", "openrouter/openai/gpt-6", "openai"),
        ("opencode", "anthropic/claude-x", "anthropic"),
    ],
)
def test_the_shipped_table_names_the_lab(provider: str, model: str, vendor: str) -> None:
    assert TABLE.resolve(provider, model) == vendor


@pytest.mark.parametrize(
    ("provider", "model"),
    [
        ("openrouter", "unlisted/gpt-x"),  # no inference from the name under "org"
        ("openrouter", "deepseek"),  # no slash
        ("openrouter", "deepseek/"),  # empty name
        ("openrouter", "a/b/c"),  # "org" is exactly two segments
        ("agy", "google/gemini-x"),  # a slash under "bare"
        ("agy", ""),
        ("opencode", ""),
        ("opencode", "someprovider/model"),
        ("opencode", "openrouter/unlisted/x"),
        ("openai-compat", "anything"),
        ("nonexistent", "gpt-6"),
    ],
)
def test_what_the_table_cannot_name_is_unknown(provider: str, model: str) -> None:
    assert TABLE.resolve(provider, model) == UNKNOWN


def test_vibe_and_mistral_are_one_vendor() -> None:
    assert TABLE.resolve("vibe", "mistral-large-4") == TABLE.resolve("mistral", "mistral-small-4")


def test_a_backend_switch_makes_any_link_unknown() -> None:
    assert TABLE.resolve("codex", "gpt-6", ("OPENAI_BASE_URL",)) == UNKNOWN
    assert TABLE.resolve("claude", "claude-x", ("ANTHROPIC_BASE_URL",)) == UNKNOWN


def _operator(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "vendors.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_an_operator_pair_names_an_otherwise_unknown_model(tmp_path: Path) -> None:
    table = load_table(
        _operator(
            tmp_path,
            '[[pair]]\nprovider = "openai-compat"\nmodel = "my-model"\nvendor = "acme"\n',
        )
    )
    assert table.resolve("openai-compat", "my-model") == "acme"
    assert table.resolve("openai-compat", "other") == UNKNOWN


def test_an_operator_pair_never_wins_over_a_backend_switch(tmp_path: Path) -> None:
    table = load_table(
        _operator(tmp_path, '[[pair]]\nprovider = "agy"\nmodel = "x-1"\nvendor = "acme"\n')
    )
    assert table.resolve("agy", "x-1", ("SOMETHING",)) == UNKNOWN


def test_an_operator_pair_the_shipped_table_resolves_is_refused(tmp_path: Path) -> None:
    path = _operator(
        tmp_path,
        '[[pair]]\nprovider = "opencode"\nmodel = "opencode-go/deepseek-v4-pro"\n'
        'vendor = "openai"\n',
    )
    with pytest.raises(VendorTableError, match="already resolves"):
        load_table(path)


@pytest.mark.parametrize(
    "body",
    [
        "not toml [",
        '[[pair]]\nprovider = "x"\nmodel = "m"\n',  # no vendor
        '[[pair]]\nprovider = "x"\nmodel = "m"\nvendor = "unknown"\n',
        '[[pair]]\nprovider = ""\nmodel = "m"\nvendor = "acme"\n',
        '[[pair]]\nprovider = "x"\nmodel = "m"\nvendor = "acme"\n'
        '[[pair]]\nprovider = "x"\nmodel = "m"\nvendor = "acme"\n',  # duplicate
        '[orgs]\nfoo = "bar"\n',  # only [[pair]] is allowed
    ],
)
def test_a_malformed_operator_file_is_refused_naming_it(tmp_path: Path, body: str) -> None:
    path = _operator(tmp_path, body)
    with pytest.raises(VendorTableError, match="vendors.toml"):
        load_table(path)


def test_the_digest_changes_with_the_operator_pairs(tmp_path: Path) -> None:
    plain = load_table(None)
    extended = load_table(
        _operator(tmp_path, '[[pair]]\nprovider = "openai-compat"\nmodel = "m"\nvendor = "a"\n')
    )
    assert plain.digest.startswith("sha256:")
    assert plain.digest == shipped_table().digest
    assert plain.digest != extended.digest
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest tests/unit/headless_agents/test_model_vendor.py -q`
Expected: FAIL (`ModuleNotFoundError: headless_agents.model_vendor`).

- [ ] **Step 4: Implement `model_vendor.py`** (resolution and loading; `backend_switches`
  comes in Task 2):

```python
"""Which lab trained a model: the shipped table and the operator's exact pairs.

Spec 2026-10-13 model-vendor rule, §2 D1-D3 and §3.1-§3.2. Pure: no provider is
called, nothing is written.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from importlib import resources
from pathlib import Path
from typing import Final

UNKNOWN: Final = "unknown"
VENDORS_FILE_NAME: Final = "vendors.toml"
_GRAMMARS: Final = frozenset({"org", "bare", "opencode"})
_OPENCODE_GRAMMARS: Final = frozenset({"org", "bare"})


class VendorTableError(ValueError):
    """A vendor table cannot be used; the message names the file and why."""


@dataclass(frozen=True)
class VendorTable:
    providers: Mapping[str, Mapping[str, str]]
    orgs: Mapping[str, str]
    bare: tuple[tuple[str, str], ...]
    opencode: Mapping[str, Mapping[str, str]]
    pairs: Mapping[tuple[str, str], str] = field(default_factory=dict)

    def resolve(self, provider: str, model: str, switches: Sequence[str] = ()) -> str:
        if switches:
            return UNKNOWN
        found = self._shipped(provider, model)
        if found != UNKNOWN:
            return found
        return self.pairs.get((provider, model), UNKNOWN)

    def _shipped(self, provider: str, model: str) -> str:
        entry = self.providers.get(provider)
        if entry is None:
            return UNKNOWN
        if "vendor" in entry:
            return entry["vendor"]
        return self._grammar(entry["grammar"], model)

    def _grammar(self, grammar: str, model: str) -> str:
        if grammar == "org":
            parts = model.split("/")
            if len(parts) != 2 or not all(parts):
                return UNKNOWN
            return self.orgs.get(parts[0], UNKNOWN)
        if grammar == "bare":
            if not model or "/" in model:
                return UNKNOWN
            return next((v for glob, v in self.bare if fnmatchcase(model, glob)), UNKNOWN)
        # "opencode": the first segment is opencode's own provider id.
        head, sep, rest = model.partition("/")
        entry = self.opencode.get(head) if sep and rest else None
        if entry is None:
            return UNKNOWN
        if "vendor" in entry:
            return entry["vendor"]
        return self._grammar(entry["grammar"], rest)

    @property
    def digest(self) -> str:
        document = {
            "providers": {k: dict(v) for k, v in sorted(self.providers.items())},
            "orgs": dict(sorted(self.orgs.items())),
            "bare": [list(item) for item in self.bare],
            "opencode": {k: dict(v) for k, v in sorted(self.opencode.items())},
            "pairs": sorted([p, m, v] for (p, m), v in self.pairs.items()),
        }
        raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        return "sha256:" + hashlib.sha256(raw).hexdigest()


def _text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value or value == UNKNOWN:
        raise VendorTableError(f"{where}: expected a non-empty vendor name")
    return value


def _entry(value: object, grammars: frozenset[str], where: str) -> dict[str, str]:
    if not isinstance(value, dict) or len(value) != 1:
        raise VendorTableError(f"{where}: expected exactly one of vendor or grammar")
    ((key, item),) = value.items()
    if key == "vendor":
        return {"vendor": _text(item, where)}
    if key == "grammar" and item in grammars:
        return {"grammar": str(item)}
    raise VendorTableError(f"{where}: unknown key or grammar {key}={item!r}")


def _parse_shipped(document: Mapping[str, object], where: str) -> VendorTable:
    def table(name: str) -> dict[str, object]:
        value = document.get(name)
        if not isinstance(value, dict):
            raise VendorTableError(f"{where}: [{name}] is missing or malformed")
        return value

    return VendorTable(
        providers={k: _entry(v, _GRAMMARS, f"{where} [providers] {k}") for k, v in table("providers").items()},
        orgs={k: _text(v, f"{where} [orgs] {k}") for k, v in table("orgs").items()},
        bare=tuple((k, _text(v, f"{where} [bare] {k}")) for k, v in table("bare").items()),
        opencode={k: _entry(v, _OPENCODE_GRAMMARS, f"{where} [opencode] {k}") for k, v in table("opencode").items()},
    )


def shipped_table() -> VendorTable:
    text = resources.files("headless_agents").joinpath(VENDORS_FILE_NAME).read_text("utf-8")
    return _parse_shipped(tomllib.loads(text), f"shipped {VENDORS_FILE_NAME}")


def load_table(operator_file: Path | None) -> VendorTable:
    """The shipped table, extended by the operator's exact pairs when the file exists."""
    shipped = shipped_table()
    if operator_file is None:
        return shipped
    where = str(operator_file)
    try:
        document = tomllib.loads(operator_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise VendorTableError(f"{where}: cannot be read: {exc}") from None
    if set(document) - {"pair"}:
        raise VendorTableError(f"{where}: only [[pair]] entries are allowed")
    raw_pairs = document.get("pair", [])
    if not isinstance(raw_pairs, list):
        raise VendorTableError(f"{where}: pair must be an array of tables")
    pairs: dict[tuple[str, str], str] = {}
    for index, raw in enumerate(raw_pairs, 1):
        at = f"{where} pair {index}"
        if not isinstance(raw, dict) or set(raw) != {"provider", "model", "vendor"}:
            raise VendorTableError(f"{at}: needs exactly provider, model and vendor")
        provider, model = raw["provider"], raw["model"]
        if not isinstance(provider, str) or not provider or not isinstance(model, str) or not model:
            raise VendorTableError(f"{at}: provider and model must be non-empty strings")
        key = (provider, model)
        if key in pairs:
            raise VendorTableError(f"{at}: {provider} {model} is declared twice")
        if shipped.resolve(provider, model) != UNKNOWN:
            raise VendorTableError(
                f"{at}: the shipped table already resolves {provider} {model}; remove the pair"
            )
        pairs[key] = _text(raw["vendor"], at)
    return VendorTable(shipped.providers, shipped.orgs, shipped.bare, shipped.opencode, pairs)


__all__ = [
    "UNKNOWN",
    "VENDORS_FILE_NAME",
    "VendorTable",
    "VendorTableError",
    "load_table",
    "shipped_table",
]
```

Split long lines to satisfy Ruff's line length (the repository's `ruff` config decides);
behaviour must stay exactly as written.

- [ ] **Step 5: Run the tests and `make ci`**

Run: `uv run pytest tests/unit/headless_agents/test_model_vendor.py -q` → all pass.
Run: `make ci` → green, and `unzip -l dist/headless_agents-*.whl | grep vendors.toml`
shows the table inside the wheel (hatch ships every file under the package).

- [ ] **Step 6: Commit** — `✨ feat(vendor): add the model-vendor table and its resolution`

---

### Task 2: Backend switches

**Files:**
- Modify: `src/headless_agents/model_vendor.py`
- Modify: `src/headless_agents/providers/codex.py` (`build_codex_command`, line ~136)
- Test: `tests/unit/headless_agents/test_model_vendor.py`, `tests/unit/headless_agents/test_provider_codex.py`

**Interfaces:**
- Produces:
  - `ENV_SWITCHES: Final[Mapping[str, frozenset[str]]]` = `{"codex": {"OPENAI_BASE_URL", "OPENAI_API_BASE"}, "claude": {"ANTHROPIC_BASE_URL"}}`
  - `backend_switches(provider: str, environment: Mapping[str, str]) -> tuple[str, ...]` (sorted names present; presence decides, not value)
  - `codex_argv_switches(argv: Sequence[str]) -> tuple[str, ...]`
  - `build_codex_command(...)` raises `ValueError("codex argv carries a backend switch: ...")` if its own result carries one.

- [ ] **Step 1: Write the failing tests** (append to `test_model_vendor.py`):

```python
from headless_agents.model_vendor import backend_switches, codex_argv_switches


def test_backend_switches_are_named_by_presence() -> None:
    assert backend_switches("codex", {"OPENAI_BASE_URL": "x"}) == ("OPENAI_BASE_URL",)
    assert backend_switches("codex", {"OPENAI_BASE_URL": ""}) == ("OPENAI_BASE_URL",)
    assert backend_switches("codex", {"OPENAI_API_BASE": "x", "PATH": "/bin"}) == (
        "OPENAI_API_BASE",
    )
    assert backend_switches("claude", {"ANTHROPIC_BASE_URL": "x"}) == ("ANTHROPIC_BASE_URL",)
    assert backend_switches("claude", {"OPENAI_BASE_URL": "x"}) == ()
    assert backend_switches("vibe", {"OPENAI_BASE_URL": "x", "ANTHROPIC_BASE_URL": "y"}) == ()
    assert backend_switches("codex", {}) == ()


@pytest.mark.parametrize(
    ("argv", "named"),
    [
        (["codex", "exec", "-c", "model_provider=\"oss\""], ("model_provider",)),
        (["codex", "exec", "-c", "model_providers.x.base_url=\"u\""], ("model_providers.*",)),
        (["codex", "exec", "--profile", "p"], ("--profile",)),
        (["codex", "exec", "-p", "p"], ("--profile",)),
        (["codex", "exec", "--oss"], ("--oss",)),
        (["codex", "exec", "--local-provider", "ollama"], ("--local-provider",)),
        (["codex", "exec", "-c", "features.web_search=false"], ()),
    ],
)
def test_codex_argv_switches(argv: list[str], named: tuple[str, ...]) -> None:
    assert codex_argv_switches(argv) == named
```

And in `test_provider_codex.py`, a test that every production combination of
`build_codex_command` carries no switch (reuse that file's existing fixtures for
`workspace_mode`; iterate over the read-only, write and write-with-shell workspaces and
`mcp=None`/a server), and one that monkeypatches the module's override tuple to inject
`("model_provider", "oss")` and expects `ValueError(match="backend switch")`.

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/unit/headless_agents/test_model_vendor.py tests/unit/headless_agents/test_provider_codex.py -q`
Expected: FAIL on the import of `backend_switches`.

- [ ] **Step 3: Implement** in `model_vendor.py`:

```python
ENV_SWITCHES: Final[Mapping[str, frozenset[str]]] = {
    "codex": frozenset({"OPENAI_BASE_URL", "OPENAI_API_BASE"}),
    "claude": frozenset({"ANTHROPIC_BASE_URL"}),
}
_CODEX_FLAGS: Final = {"--profile": "--profile", "-p": "--profile", "--oss": "--oss",
                       "--local-provider": "--local-provider"}


def backend_switches(provider: str, environment: Mapping[str, str]) -> tuple[str, ...]:
    """The backend-switching variables of ``provider`` present in its child environment."""
    return tuple(sorted(ENV_SWITCHES.get(provider, frozenset()) & environment.keys()))


def codex_argv_switches(argv: Sequence[str]) -> tuple[str, ...]:
    """The backend switches a codex argv carries (spec D2bis)."""
    found: set[str] = set()
    for index, arg in enumerate(argv):
        if arg in _CODEX_FLAGS:
            found.add(_CODEX_FLAGS[arg])
        if arg == "-c" and index + 1 < len(argv):
            key = argv[index + 1].split("=", 1)[0].strip()
            if key == "model_provider":
                found.add("model_provider")
            elif key.startswith("model_providers."):
                found.add("model_providers.*")
    return tuple(sorted(found))
```

Add both names to `__all__`. In `build_codex_command`, just before `return command`:

```python
    switches = codex_argv_switches(command)
    if switches:
        raise ValueError(f"codex argv carries a backend switch: {', '.join(switches)}")
```

(import `codex_argv_switches` from `..model_vendor`).

- [ ] **Step 4: Run the tests and `make ci`** → green.

- [ ] **Step 5: Commit** — `🦺 feat(vendor): name the backend switches of claude and codex`

---

### Task 3: Provenance and unconfined writers record links

**Files:**
- Modify: `src/headless_agents/model_vendor.py` (add `LinkRecord`)
- Modify: `src/headless_agents/provenance.py` (`record`, line ~29)
- Modify: `src/headless_agents/write_flow.py` (`_publish_unconfined_intent` ~line 418; both `provenance.record` calls ~874 and ~1080)
- Test: `tests/unit/headless_agents/test_write_flow.py`, `tests/unit/headless_agents/test_vendor.py` (helpers), a new `tests/unit/headless_agents/test_provenance_links.py`

**Interfaces:**
- Consumes: `backend_switches` (Task 2).
- Produces:
  - `@dataclass(frozen=True) class LinkRecord: provider: str; model: str; switches: tuple[str, ...]` with `to_document() -> dict[str, object]` and `@classmethod from_document(raw: object) -> LinkRecord | None` (None when malformed).
  - `role_links(role: Role, models: Mapping[str, str], environment: Mapping[str, str]) -> tuple[LinkRecord, ...]` in `model_vendor.py` — one per `role.links`, model from `models.get(link.provider, "")`, switches from `backend_switches(link.provider, environment)`.
  - `provenance.record(..., providers: Sequence[str], links: Sequence[LinkRecord]) -> None` writes `"links": [l.to_document() for l in links]`; `links` is keyword-only and required.
  - The unconfined writer record gains `"links"`, the same shape.

- [ ] **Step 1: Write the failing tests**

```python
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
        tmp_path, SHA, run_id="r", lineage="r", made_by="engine",
        providers=["codex", "opencode"], links=links,
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
    for bad in (
        None, [], {"provider": "", "model": "m", "switches": []},
        {"provider": "p", "model": 1, "switches": []},
        {"provider": "p", "model": "m", "switches": [""]},
        {"provider": "p", "model": "m"},
    ):
        assert LinkRecord.from_document(bad) is None
```

In `test_write_flow.py`, extend the existing test that inspects the provenance of a
successful write (search for `provenance.lookup`) to assert `links` equals the plan's
links, and the existing unconfined-writer test (search for `unconfined-writers.json`) to
assert the writer record carries `links`. Add one test where the plan's `environment`
carries `OPENAI_BASE_URL` and a codex link: the recorded switches name it. Add one test
that the write's `--model` override (the plan's `models` snapshot) is what `links`
records, and that rewriting `models.toml` after planning does not change it (build the
plan, rewrite the file, then run the write).

- [ ] **Step 2: Run to see them fail** — `uv run pytest tests/unit/headless_agents/test_provenance_links.py tests/unit/headless_agents/test_write_flow.py -q` → FAIL.

- [ ] **Step 3: Implement**

In `model_vendor.py`:

```python
@dataclass(frozen=True)
class LinkRecord:
    """One link of a writing or reviewing role: provider, frozen model, backend switches."""

    provider: str
    model: str
    switches: tuple[str, ...]

    def to_document(self) -> dict[str, object]:
        return {"provider": self.provider, "model": self.model, "switches": list(self.switches)}

    @classmethod
    def from_document(cls, raw: object) -> LinkRecord | None:
        if not isinstance(raw, dict) or set(raw) != {"provider", "model", "switches"}:
            return None
        provider, model, switches = raw["provider"], raw["model"], raw["switches"]
        if (
            not isinstance(provider, str) or not provider
            or not isinstance(model, str)
            or not isinstance(switches, list)
            or not all(isinstance(s, str) and s for s in switches)
        ):
            return None
        return cls(provider, model, tuple(switches))


def role_links(
    role: Role, models: Mapping[str, str], environment: Mapping[str, str]
) -> tuple[LinkRecord, ...]:
    return tuple(
        LinkRecord(link.provider, models.get(link.provider, ""),
                   backend_switches(link.provider, environment))
        for link in role.links
    )
```

(`from .roles import Role` under `TYPE_CHECKING` to avoid an import cycle.)

`provenance.record` gains `links: Sequence[LinkRecord]` (keyword-only) and stores
`"links": [link.to_document() for link in links]`. In `write_flow.py`, both record calls
pass `links=role_links(<plan>.role, <plan>.models, <plan>.environment)` where `<plan>` is
`write.plan` (line ~874) and `plan` (line ~1080); `_publish_unconfined_intent` adds
`"links": [l.to_document() for l in role_links(write.plan.role, write.plan.models, write.plan.environment)]`.
Update the `_record` and `_writers` helpers of `test_vendor.py` to pass/emit links.

- [ ] **Step 4: Run the tests and `make ci`** → green.

- [ ] **Step 5: Commit** — `✨ feat(provenance): record each link with its model and switches`

---

### Task 4: The rule by vendor, the new `VendorCheck` and legacy documents

**Files:**
- Modify: `src/headless_agents/model_vendor.py` (add `ResolvedLink`)
- Modify: `src/headless_agents/vendor.py` (whole module)
- Modify: `src/headless_agents/reviews.py` (`load_check`, `load_result`)
- Test: `tests/unit/headless_agents/test_vendor.py`, `tests/unit/headless_agents/test_reviews.py`

**Interfaces:**
- Consumes: `VendorTable`, `UNKNOWN`, `LinkRecord` (Tasks 1, 3).
- Produces:
  - `model_vendor.ResolvedLink(provider: str, model: str, switches: tuple[str, ...], vendor: str)` with `to_document()` / `from_document(raw) -> ResolvedLink | None` (vendor a non-empty string).
  - `vendor.AttributedCommit(sha, run_id, made_by, providers, links: tuple[LinkRecord, ...] | None)` — `None` only for legacy (no `links` recorded); `()` for a hand-written commit.
  - `vendor.PanelMember(slot: str, role: str, links: tuple[LinkRecord, ...])`, `slot` in `{"review", "judge"}`.
  - `vendor.check_independence(commits: Sequence[AttributedCommit], panel: Sequence[PanelMember], table: VendorTable) -> VendorCheck`.
  - `vendor.VendorCheck(commits: tuple[CheckedCommit, ...], panel: tuple[CheckedMember, ...], authors: tuple[str, ...], table_digest: str)`; `to_document()` emits `"rule": "model-vendor"`.
  - `vendor.CheckedCommit(sha, run_id, made_by, providers, links: tuple[ResolvedLink, ...])`, `vendor.CheckedMember(slot, role, links: tuple[ResolvedLink, ...])`.
  - `vendor.LegacyVendorCheck` — today's `VendorCheck`, renamed, unchanged behaviour.
  - `vendor.check_from_document(document: Mapping[str, object], *, where: str) -> VendorCheck | LegacyVendorCheck`.

- [ ] **Step 1: Write the failing tests** (rewrite `test_vendor.py` around links; keep
  every existing refusal test for malformed records and unreadable writers):

```python
from headless_agents.model_vendor import LinkRecord, shipped_table
from headless_agents.vendor import (
    PanelMember, VendorRefused, attribute, check_from_document, check_independence,
)

TABLE = shipped_table()


def _link(provider: str, model: str = "", *switches: str) -> LinkRecord:
    return LinkRecord(provider, model, tuple(switches))


def _record(state, sha, *links: LinkRecord, made_by="engine"):
    provenance.record(state, sha, run_id=RUN, lineage=RUN, made_by=made_by,
                      providers=[l.provider for l in links], links=links)


def _panel(*members: tuple[str, str, LinkRecord]) -> list[PanelMember]:
    return [PanelMember(slot, role, (link,)) for slot, role, link in members]


def test_same_lab_through_another_provider_is_refused(tmp_path) -> None:
    _record(tmp_path, SHA_1, _link("vibe", "mistral-large-4"))
    commits = attribute(tmp_path, [(SHA_1, "feat: x")])
    with pytest.raises(VendorRefused, match="mistral"):
        check_independence(commits, _panel(("review", "r", _link("mistral", "mistral-small-4"))), TABLE)


@pytest.mark.parametrize(
    ("author", "reviewer"),
    [
        (_link("claude", "claude-x"), _link("openrouter", "anthropic/claude-y")),
        (_link("codex", "gpt-6"), _link("opencode", "openrouter/openai/gpt-6")),
        (_link("opencode", "openrouter/deepseek/deepseek-v4-pro"), _link("opencode", "opencode-go/deepseek-v4-pro")),
    ],
)
def test_cross_router_pairs_of_one_lab_are_refused(tmp_path, author, reviewer) -> None:
    _record(tmp_path, SHA_1, author)
    with pytest.raises(VendorRefused):
        check_independence(attribute(tmp_path, [(SHA_1, "x")]), _panel(("review", "r", reviewer)), TABLE)


def test_same_provider_different_labs_passes(tmp_path) -> None:
    _record(tmp_path, SHA_1, _link("opencode", "opencode-go/deepseek-v4-pro"))
    check = check_independence(
        attribute(tmp_path, [(SHA_1, "x")]),
        _panel(("review", "r", _link("opencode", "anthropic/claude-x")),
               ("judge", "j", _link("agy", "gemini-3.8-flash-high"))),
        TABLE,
    )
    assert check.authors == ("deepseek",)
    assert check_from_document(check.to_document(), where="t") == check


def test_the_judge_is_under_the_rule(tmp_path) -> None:
    _record(tmp_path, SHA_1, _link("codex", "gpt-6"))
    with pytest.raises(VendorRefused, match="judge"):
        check_independence(
            attribute(tmp_path, [(SHA_1, "x")]),
            _panel(("review", "r", _link("agy", "gemini-x")), ("judge", "j", _link("codex", "gpt-6"))),
            TABLE,
        )


def test_an_unknown_panel_vendor_is_refused_with_the_line_to_add(tmp_path) -> None:
    _record(tmp_path, SHA_1, _link("codex", "gpt-6"))
    with pytest.raises(VendorRefused, match=r"vendors\.toml"):
        check_independence(attribute(tmp_path, [(SHA_1, "x")]),
                           _panel(("review", "r", _link("openai-compat", "my-model"))), TABLE)


def test_a_switched_panel_member_is_refused(tmp_path) -> None:
    _record(tmp_path, SHA_1, _link("agy", "gemini-x"))
    with pytest.raises(VendorRefused, match="OPENAI_BASE_URL"):
        check_independence(attribute(tmp_path, [(SHA_1, "x")]),
                           _panel(("review", "r", _link("codex", "gpt-6", "OPENAI_BASE_URL"))), TABLE)


def test_an_unknown_author_refuses(tmp_path) -> None:
    _record(tmp_path, SHA_1, _link("openai-compat", "m"))
    with pytest.raises(VendorRefused, match="unknown"):
        check_independence(attribute(tmp_path, [(SHA_1, "x")]),
                           _panel(("review", "r", _link("agy", "gemini-x"))), TABLE)


def test_a_legacy_record_refuses_as_predating_model_provenance(tmp_path) -> None:
    publish(tmp_path / "provenance" / f"{SHA_1}.json",
            {"sha": SHA_1, "run_id": RUN, "lineage": RUN, "made_by": "engine", "providers": ["claude"]})
    (commit,) = attribute(tmp_path, [(SHA_1, "x")])
    assert commit.links is None
    with pytest.raises(VendorRefused, match="predates model provenance"):
        check_independence((commit,), _panel(("review", "r", _link("agy", "gemini-x"))), TABLE)


@pytest.mark.parametrize(
    "links",
    [
        [],
        [{"provider": "codex", "model": "m", "switches": []}],  # opencode missing
        [{"provider": "opencode", "model": "m", "switches": []},
         {"provider": "codex", "model": "m", "switches": []}],  # reordered
        [{"provider": "codex", "model": 1, "switches": []},
         {"provider": "opencode", "model": "m", "switches": []}],
    ],
)
def test_present_but_incomplete_links_refuse(tmp_path, links) -> None:
    publish(tmp_path / "provenance" / f"{SHA_1}.json",
            {"sha": SHA_1, "run_id": RUN, "lineage": RUN, "made_by": "engine",
             "providers": ["codex", "opencode"], "links": links})
    with pytest.raises(VendorRefused, match="malformed"):
        attribute(tmp_path, [(SHA_1, "x")])


def test_every_link_of_a_fallback_chain_is_checked(tmp_path) -> None:
    _record(tmp_path, SHA_1, _link("codex", "gpt-6"))
    member = PanelMember("review", "chain", (_link("agy", "gemini-x"), _link("openrouter", "openai/gpt-6")))
    with pytest.raises(VendorRefused, match="openai"):
        check_independence(attribute(tmp_path, [(SHA_1, "x")]), [member], TABLE)


def test_unconfined_writers_without_links_make_unknown_authors(tmp_path) -> None:
    publish(tmp_path / "unconfined-writers.json",
            {"writers": [{"run_id": OTHER, "repository": "/r", "providers": ["opencode"]}]})
    (commit,) = attribute(tmp_path, [(SHA_1, "feat: by hand?")])
    assert commit.made_by == "unknown" and commit.links is None
```

Document validation tests (in `test_vendor.py`), each mutating a valid
`check.to_document()` and expecting `Unknown` from `check_from_document`:
`authors` set to `["openai"]` while the only link says `anthropic`; a link without
`vendor`; an author link with vendor `unknown`; a panel link with vendor `unknown`;
a panel vendor in `authors`; `panel` empty; a panel member with `links: []`; a commit with
`made_by: "engine"` and `links: []`; a commit whose links' providers differ from its
`providers`. Plus: a legacy document (today's shape, copied from an existing fixture in
`test_reviews.py`) reads as `LegacyVendorCheck` and round-trips.

In `test_reviews.py`: the same `authors`-mismatch document and the empty-panel document
written as a review result are refused by `reviews.load_result` (`Unknown`), and a legacy
result still loads.

- [ ] **Step 2: Run to see them fail** — `uv run pytest tests/unit/headless_agents/test_vendor.py tests/unit/headless_agents/test_reviews.py -q` → FAIL.

- [ ] **Step 3: Implement**

`model_vendor.ResolvedLink` mirrors `LinkRecord` plus `vendor: str` (non-empty), with the
same `to_document`/`from_document` pattern (`set(raw) == {"provider", "model", "switches", "vendor"}`).

In `vendor.py`:

1. Rename today's `VendorCheck` to `LegacyVendorCheck` (body unchanged).
2. `AttributedCommit` gains `links: tuple[LinkRecord, ...] | None`; `to_document` is no
   longer used for new checks (CheckedCommit carries resolved links).
3. `_recorded(sha, record)`: after today's checks, read `record.get("links")`:
   absent → `links=None`; present → every entry through `LinkRecord.from_document`, and
   `[l.provider for l in links] == providers`, non-empty; otherwise `VendorRefused(f"the
   provenance record of {sha[:12]} is malformed: ...")`.
4. `_unconfined_providers` becomes `_unconfined_writers(state) -> tuple[tuple[str, ...],
   tuple[LinkRecord, ...] | None]`: providers as today; links = de-duplicated union of
   every writer's links in order, or `None` as soon as one writer has no `links`; a present
   but malformed `links` raises `VendorRefused(... malformed ...)`.
5. `attribute`: recorded → `_recorded`; hand (`made_by="hand"`) → `links=()`;
   unconfined → `links` from step 4.
6. `check_independence(commits, panel, table)`:

```python
def _resolve(link: LinkRecord, table: VendorTable) -> ResolvedLink:
    return ResolvedLink(link.provider, link.model, link.switches,
                        table.resolve(link.provider, link.model, link.switches))


def _why_unknown(link: ResolvedLink) -> str:
    if link.switches:
        return f"its environment sets {', '.join(link.switches)}, which can point it at another backend"
    return (
        f"its vendor cannot be named; declare it in vendors.toml: "
        f'[[pair]] provider = "{link.provider}" model = "{link.model}" vendor = "<lab>"'
    )


def check_independence(
    commits: Sequence[AttributedCommit], panel: Sequence[PanelMember], table: VendorTable
) -> VendorCheck:
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
        checked.append(CheckedCommit(commit.sha, commit.run_id, commit.made_by, commit.providers, resolved))
    members: list[CheckedMember] = []
    for member in panel:
        resolved = tuple(_resolve(link, table) for link in member.links)
        for link in resolved:
            label = f"{member.slot} {member.role} runs {link.provider} {link.model or '(default)'}"
            if link.vendor == UNKNOWN:
                raise VendorRefused(f"{label}: {_why_unknown(link)}")
            if link.vendor in authors:
                sha = next(c.sha for c in checked if any(l.vendor == link.vendor for l in c.links))
                raise VendorRefused(
                    f"{label}, a {link.vendor} model, and {link.vendor} wrote commit {sha[:12]} "
                    "of the reviewed range: no reviewer or judge may share a lab with the author"
                )
        members.append(CheckedMember(member.slot, member.role, resolved))
    return VendorCheck(tuple(checked), tuple(members), tuple(sorted(authors)), table.digest)
```

7. `VendorCheck.to_document()`:
   `{"rule": "model-vendor", "table_digest": ..., "authors": [...], "commits": [{"sha", "run_id", "made_by", "providers", "links": [ResolvedLink docs]}], "panel": [{"slot", "role", "links": [...]}]}`.
8. `VendorCheck.from_document(document, *, where)` raises `Unknown(f"{where}: vendor check is malformed")` unless all of:
   every commit's `sha` matches `_SHA`, `made_by` in `MADE_BY`, `run_id` `None` or
   non-empty str; `links` parse as `ResolvedLink`s; `links == ()` only when
   `made_by == "hand"` and `providers == ()`; otherwise `[l.provider for l in links] ==
   providers`; `authors == sorted({l.vendor for c in commits for l in c.links})`; no
   author vendor is `unknown`; `panel` non-empty; each member's `slot` in
   `{"review", "judge"}`, `role` non-empty, `links` non-empty, no vendor `unknown` and none
   in `authors`; `table_digest` starts with `sha256:`.
9. `check_from_document(document, *, where)`: `"rule" in document` →
   `VendorCheck.from_document` (and `rule` must equal `"model-vendor"`), else
   `LegacyVendorCheck.from_document`.
10. `reviews.load_check` / `load_result` call `check_from_document`; their return type
    becomes `VendorCheck | LegacyVendorCheck`; `write_check(state, run_id, check: VendorCheck)`.

Export the new names in `__all__`.

- [ ] **Step 4: Run the tests and `make ci`** → green (mypy included).

- [ ] **Step 5: Commit** — `✨ feat(vendor): judge independence by the model's lab`

---

### Task 5: Wire the panel, the table and `ha show`

**Files:**
- Modify: `src/headless_agents/review_flow.py` (`prepare`/`_prepare`: replace `reviewers: Mapping[str, Sequence[str]]` with `panel: Sequence[PanelMember]` and add `table: VendorTable`)
- Modify: `src/headless_agents/engine.py` (review run ~line 1340; planning where `plan.panel` is built — load the table there)
- Modify: `src/headless_agents/show.py` (`vendors_line`, line ~458)
- Modify: `README.md` (vendor-rule paragraph ~line 676), `CHANGELOG.md` (`## Unreleased`)
- Test: `tests/unit/headless_agents/test_review.py`, `tests/unit/headless_agents/test_engine_execute.py`, `tests/unit/headless_agents/test_engine_plan.py`, the `show` tests (search `vendors_line`)

**Interfaces:**
- Consumes: `PanelMember`, `check_independence`, `load_table`, `VENDORS_FILE_NAME`, `role_links`.
- Produces: `engine.panel_members(plan: Plan) -> tuple[PanelMember, ...]` —
  `PanelMember(slot.slot, slot.role.name, role_links(slot.role, slot.models, slot.environment))`
  for every slot of `plan.panel`, judge included, in launch order.

- [ ] **Step 1: Write the failing tests**

- `test_engine_plan.py`: `panel_members` of a planned review returns the reviewers then
  the judge, each link carrying the model of its `SlotPlan.models` — for a model from a
  chain link, from the role's `model`, and from `models.toml`'s default; and rewriting
  `models.toml` after planning leaves `panel_members(plan)` unchanged.
- `test_engine_execute.py` (or `test_review.py`, following where review execution is
  tested today): (a) an author commit recorded on `vibe` and a judge on `mistral` refuse
  the review with exit 2 and **no reviewer step started** (assert no step directory /
  no fake-provider invocation, whichever the existing tests use); (b) `environ` carrying
  `ANTHROPIC_BASE_URL` with a `claude` reviewer refuses at admission, the variable
  passing through `operator_environment`; (c) `environ` carrying
  `CLAUDE_CODE_USE_BEDROCK=1` with a `claude` reviewer does **not** refuse (it is
  filtered); (d) an operator `vendors.toml` that is not TOML refuses with exit 2 naming
  the file; (e) the existing test that a review workflow refuses `--model` still passes
  unchanged.
- `show` tests: `vendors_line` renders a new-shape check as
  `vendors authors deepseek (1 recorded commit) — panel review opencode anthropic, judge agy google: independent`
  and still renders a legacy check exactly as today.

- [ ] **Step 2: Run to see them fail** — the four test files above → FAIL.

- [ ] **Step 3: Implement**

- `engine.py`: add `panel_members`; where the review plan is built, load
  `table = load_table(config_file(VENDORS_FILE_NAME, request.environ, home=request.home))`
  and turn `VendorTableError` into `UsageError(str(exc))` (exit 2, before anything runs).
  Keep the table on the plan (`Plan.vendor_table: VendorTable | None = None`). The review
  run passes `panel=panel_members(plan), table=plan.vendor_table` to
  `review_flow.prepare` instead of `reviewers=...`.
- `review_flow._prepare`: `check = check_independence(attribute(state, _range_commits(log)), panel, table)`.
- `show.vendors_line`: when `check.get("rule") == "model-vendor"`, render authors (lab
  names) and the panel members `"<slot> <provider> <vendor>"` joined by `, `; otherwise
  today's code path unchanged.
- `README.md`: replace "no reviewer shares a provider with whoever wrote a commit of the
  range" by "no reviewer or judge runs a model from the lab that wrote a commit of the
  range (vendors resolved from the model, not the provider; see
  `docs/specs/2026-10-13-model-vendor-rule-design.md`)", and add one paragraph on
  `vendors.toml` exact pairs with an example.
- `CHANGELOG.md` `## Unreleased`: a `### Changed` entry (**breaking** for reviews: the
  vendor rule compares model labs and covers the judge; reviews of ranges holding commits
  recorded before this change are refused until those commits leave the range;
  `VendorCheck` documents gain the `model-vendor` shape, legacy documents still read) and
  an `### Added` entry for `vendors.toml`.

- [ ] **Step 4: Run the full suite** — `make ci` → green; `rail check` → green.

- [ ] **Step 5: Commit** — `✨ feat(review): check the whole panel by model lab before it starts`

---

## Execution notes

- Order is strict: 1 → 2 → 3 → 4 → 5 (each consumes the previous interfaces).
- After Task 5: `red-review`-style review of `git diff main...HEAD`, then PR, rail
  reviewer, independent non-Claude judge on the exact head (c2c46a2a), coordination go.
- No release, tag or version bump in this lot (operator decision).
