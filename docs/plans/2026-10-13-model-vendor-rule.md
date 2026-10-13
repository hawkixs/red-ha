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
| `src/headless_agents/review_flow.py`, `engine.py` (modify) | panel with judge from the slot snapshot; table loaded at planning (Task 4) |
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
  - `refuse_argv_switches(argv: Sequence[str]) -> None` in `providers/codex.py`: raises
    `ValueError("codex argv carries a backend switch: ...")` when `codex_argv_switches`
    names any; `build_codex_command` calls it on the argv it is about to return (spec
    amendment A1: an argv switch is refused where it is built, never recorded).

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

And in `test_provider_codex.py`:

```python
from headless_agents.providers.codex import refuse_argv_switches


@pytest.mark.parametrize(
    "argv",
    [
        ["codex", "exec", "-c", 'model_provider="oss"', "-"],
        ["codex", "exec", "--oss", "-"],
        ["codex", "exec", "-p", "work", "-"],
    ],
)
def test_an_argv_with_a_backend_switch_is_refused(argv: list[str]) -> None:
    with pytest.raises(ValueError, match="backend switch"):
        refuse_argv_switches(argv)


def test_a_clean_argv_passes() -> None:
    refuse_argv_switches(["codex", "exec", "-c", "features.web_search=false", "-"])
```

plus one test that `build_codex_command` output carries no switch for every production
combination: iterate over the workspace modes this file's existing tests already build
(read-only, write, write with shell) and `mcp=None` or the file's existing MCP server
fixture, asserting `codex_argv_switches(command) == ()`; and one test that
`build_codex_command` calls the guard (monkeypatch `headless_agents.providers.codex.
refuse_argv_switches` with a recorder and assert it received the returned argv).

And in `test_provider_claude.py` and `test_provider_codex.py`, the production
child-environment path of each provider (the environment the engine hands a run is
`operator_environment(environ)`, and both providers return it as their child
environment):

```python
from headless_agents.engine import operator_environment
from headless_agents.model_vendor import backend_switches


def test_the_production_child_environment_carries_only_real_switches() -> None:
    clean = operator_environment({"PATH": "/bin", "HOME": "/h", "CLAUDE_CODE_USE_BEDROCK": "1"})
    spec = RunSpec(prompt="P", model="m", environment=clean)
    env = claude.ClaudeProvider().child_environment(spec, {})
    assert env is not None and backend_switches("claude", env) == ()
    claude.ClaudeProvider().build_command(spec)  # builds; claude has no argv switch (A1)
    switched = RunSpec(
        prompt="P", model="m",
        environment=operator_environment({"PATH": "/bin", "ANTHROPIC_BASE_URL": "http://x"}),
    )
    env = claude.ClaudeProvider().child_environment(switched, {})
    assert env is not None and backend_switches("claude", env) == ("ANTHROPIC_BASE_URL",)
```

(and the codex twin in `test_provider_codex.py` with `CodexProvider`, `OPENAI_BASE_URL`,
and the `report_log` its `build_command` requires, as that file's existing specs set it).

And in `test_model_vendor.py`, the clean production environment of `claude` and `codex`:

```python
from headless_agents.engine import operator_environment


def test_a_clean_operator_environment_has_no_switch() -> None:
    environment = operator_environment(
        {"PATH": "/bin", "HOME": "/h", "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_USE_VERTEX": "1"}
    )
    assert backend_switches("claude", environment) == ()
    assert backend_switches("codex", environment) == ()
```

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

Add both names to `__all__`. In `providers/codex.py` (import `codex_argv_switches` from
`..model_vendor`):

```python
def refuse_argv_switches(argv: Sequence[str]) -> None:
    """Refuse an argv that could point codex at another backend (spec amendment A1)."""
    switches = codex_argv_switches(argv)
    if switches:
        raise ValueError(f"codex argv carries a backend switch: {', '.join(switches)}")
```

and in `build_codex_command`, just before `return command`: `refuse_argv_switches(command)`.

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
Migrate, in this same commit, every existing caller the required `links` argument breaks:
- `tests/unit/headless_agents/test_vendor.py`: the `_record` helper passes
  `links=tuple(LinkRecord(p, "", ()) for p in providers)`; `_writers` emits
  `"links": [{"provider": p, "model": "", "switches": []} for p in providers]` per writer.
- `tests/unit/headless_agents/test_lineage.py:162`, `:173`, `:181`: pass
  `links=(LinkRecord("codex", "", ()),)` (or one per provider of the call) and add the
  `"links"` key to the expected dictionary at `:164`.
- `tests/unit/headless_agents/test_show.py:116` and `:119`: pass `links=()` for the call
  with `providers=[]` and `links=(LinkRecord("codex", "", ()),)` for the other.
Run `grep -rn "provenance.record(" src tests` before committing: no call without `links`.

- [ ] **Step 4: Run the tests and `make ci`** → green.

- [ ] **Step 5: Commit** — `✨ feat(provenance): record each link with its model and switches`

---

### Task 4: The rule by vendor, its documents, and the panel wired to it

One commit: the new `check_independence` signature and its only caller
(`review_flow._prepare`, fed by the engine) change together, so `make ci` stays green.

**Files:**
- Modify: `src/headless_agents/model_vendor.py` (add `ResolvedLink`)
- Modify: `src/headless_agents/vendor.py` (whole module)
- Modify: `src/headless_agents/reviews.py` (`ReviewResult.check`, `load_check`, `load_result`, `write_check`)
- Modify: `src/headless_agents/review_flow.py` (`prepare`/`_prepare`: `reviewers` → `panel`, add `table`)
- Modify: `src/headless_agents/engine.py` (`Plan.vendor_table`, review planning, `panel_members`, the review run ~line 1340)
- Test: `tests/unit/headless_agents/test_vendor.py`, `test_reviews.py`, `test_review.py`, `test_engine_plan.py`, `test_show.py` (fixture migration only)

**Interfaces:**
- Consumes: `VendorTable`, `UNKNOWN`, `LinkRecord` (Tasks 1, 3).
- Produces:
  - `model_vendor.ResolvedLink(provider: str, model: str, switches: tuple[str, ...], vendor: str)` with `to_document()` / `from_document(raw) -> ResolvedLink | None` (vendor a non-empty string).
  - `vendor.AttributedCommit(sha, run_id, made_by, providers, links: tuple[LinkRecord, ...] | None = None)` — `None` for legacy (no `links` recorded) and in legacy documents, whose decoder keeps constructing it without `links`; `()` for a hand-written commit.
  - `vendor.PanelMember(slot: str, role: str, links: tuple[LinkRecord, ...])`, `slot` in `{"review", "judge"}`.
  - `vendor.check_independence(commits: Sequence[AttributedCommit], panel: Sequence[PanelMember], table: VendorTable) -> VendorCheck`.
  - `vendor.VendorCheck(commits: tuple[CheckedCommit, ...], panel: tuple[CheckedMember, ...], authors: tuple[str, ...], table_digest: str)`; `to_document()` emits `"rule": "model-vendor"`.
  - `vendor.CheckedCommit(sha, run_id, made_by, providers, links: tuple[ResolvedLink, ...])`, `vendor.CheckedMember(slot, role, links: tuple[ResolvedLink, ...])`.
  - `vendor.LegacyVendorCheck` — today's `VendorCheck`, renamed, unchanged behaviour.
  - `vendor.check_from_document(document: Mapping[str, object], *, where: str) -> VendorCheck | LegacyVendorCheck`.
  - `vendor.AnyVendorCheck = VendorCheck | LegacyVendorCheck` (type alias).
  - `reviews.ReviewResult.check: AnyVendorCheck`; `reviews.load_check(...) -> AnyVendorCheck | None` (still `None` when no check was written); `reviews.write_check(state, run_id, check: AnyVendorCheck)` and `reviews.write_result(..., check: AnyVendorCheck)` (the engine only ever passes a `VendorCheck`; the legacy type stays writable so the existing legacy fixtures keep testing the legacy read path).
  - `engine.Plan.vendor_table: VendorTable | None = None` — set on every review plan, `None` otherwise (pinned by a test).
  - `engine.panel_members(plan: Plan) -> tuple[PanelMember, ...]` — `PanelMember(slot.slot, slot.role.name, role_links(slot.role, slot.models, slot.environment))` for every slot of `plan.panel`, reviewers then judge.
  - `review_flow.prepare(..., panel: Sequence[PanelMember], table: VendorTable, ...)` replaces `reviewers=`.

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

Two unconfined writers on one provider with two models attribute one commit carrying
both links, and its check round-trips:

```python
def test_two_unconfined_writers_on_one_provider_keep_both_models(tmp_path) -> None:
    publish(tmp_path / "unconfined-writers.json", {"writers": [
        {"run_id": OTHER, "repository": "/r", "providers": ["opencode"],
         "links": [{"provider": "opencode", "model": "opencode-go/glm-5", "switches": []}]},
        {"run_id": RUN, "repository": "/r", "providers": ["opencode"],
         "links": [{"provider": "opencode", "model": "opencode-go/deepseek-v4-pro", "switches": []}]},
    ]})
    (commit,) = attribute(tmp_path, [(SHA_1, "feat: x")])
    assert commit.providers == ("opencode", "opencode")
    check = check_independence((commit,), _panel(("review", "r", _link("agy", "gemini-x"))), TABLE)
    assert check.authors == ("deepseek", "zhipu")
    assert check_from_document(check.to_document(), where="t") == check
```

In `test_reviews.py`, every invalid new-shape case goes through **both** readers:

```python
@pytest.mark.parametrize("mutate", INVALID_MUTATIONS, ids=INVALID_IDS)
def test_an_invalid_new_shape_check_is_refused_by_both_readers(tmp_path, mutate) -> None:
    document = VALID_CHECK.to_document()
    mutate(document)
    with pytest.raises(Unknown):
        check_from_document(document, where="t")
    # The file's own pattern (test_a_result_ha_never_writes_is_unknown): write a valid
    # result, then replace its vendor_check with the mutated document.
    reviews.write_result(tmp_path, RUN, head=HEAD, verdict="approve", text="ok", check=VALID_CHECK)
    path = reviews.result_path(tmp_path, RUN)
    stored = json.loads(path.read_text())
    stored["vendor_check"] = document
    path.chmod(0o600)
    path.write_text(json.dumps(stored))
    with pytest.raises(Unknown):
        reviews.load_result(tmp_path, RUN)
```

where `INVALID_MUTATIONS` holds the nine mutations listed above (authors mismatch, link
without vendor, unknown author vendor, unknown panel vendor, panel vendor in authors,
empty panel, panel member without links, non-hand commit without links, links not
matching providers), and a legacy result (the file's existing `CHECK`, now a
`LegacyVendorCheck`) still loads through `load_result` and `load_check`.

Engine and admission tests:
- `test_engine_plan.py`: a planned review has `plan.vendor_table is not None`, a role run
  has `None`; `panel_members(plan)` returns the reviewers then the judge, each link
  carrying its `SlotPlan.models` value — for a model set on a chain link, on the role,
  and from `models.toml`'s default — and rewriting `models.toml` after planning leaves
  `panel_members(plan)` unchanged; an operator `vendors.toml` that is not TOML makes
  planning raise `UsageError` naming the file.
- `test_review.py`: (a) an author commit recorded on `vibe` and a judge on `mistral`
  refuse the review with exit 2 and no reviewer started (assert on the world's fake
  agents' recorded `specs`, as the existing tests do); (b) `environ` carrying
  `ANTHROPIC_BASE_URL` with a `claude` reviewer refuses at admission (the variable passes
  `operator_environment`), and the same with `OPENAI_BASE_URL` and a `codex` judge;
  (c) `CLAUDE_CODE_USE_BEDROCK=1` with a `claude` reviewer does not refuse; (d) the
  existing refusal of `--model` on a review workflow keeps passing unchanged.

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
   tuple[LinkRecord, ...] | None]`. When every writer has `links`: links = the
   de-duplicated union (by the whole record) of every writer's links in order, and
   providers = `tuple(link.provider for link in links)` — so providers and links always
   correspond one to one, a provider repeating when it ran two models. As soon as one
   writer has no `links`: providers = today's de-duplicated union and links = `None`
   (legacy, authors unknown). A present but malformed `links` raises
   `VendorRefused(... malformed ...)`; a writer's links whose providers differ from its
   `providers` are malformed too.
5. `attribute`: recorded → `_recorded`; hand (`made_by="hand"`) → `links=()`;
   unconfined → `links` from step 4.
6. `check_independence(commits, panel, table)`:

```python
def _resolve(link: LinkRecord, table: VendorTable) -> ResolvedLink:
    return ResolvedLink(link.provider, link.model, link.switches,
                        table.resolve(link.provider, link.model, link.switches))


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
10. `reviews`: `ReviewResult.check: AnyVendorCheck`; `load_check` and `load_result` call
    `check_from_document`; `load_check` keeps returning `None` when no check was written;
    `write_check` and `write_result` take `AnyVendorCheck`.
11. `engine.py`: add `vendor_table: VendorTable | None = None` to `Plan`; where a review
    plan's `panel` is built, load
    `load_table(config_file(VENDORS_FILE_NAME, request.environ, home=request.home))`,
    turning `VendorTableError` into `UsageError(str(exc))`, and set it on the plan; add
    `panel_members`. In the review run, narrow once:

    ```python
    table = plan.vendor_table
    if table is None:  # every review plan loads its table (test_engine_plan pins it)
        raise UsageError("internal: a review plan without its vendor table; nothing ran")
    ```

    and call `review_flow.prepare(..., panel=panel_members(plan), table=table, ...)`.
12. `review_flow._prepare`: `check = check_independence(attribute(state, _range_commits(log)), panel, table)`;
    `prepare`/`_prepare` take `panel: Sequence[PanelMember]` and `table: VendorTable`
    instead of `reviewers`. The `fake_prepare` in `test_review.py:664` accepts the new
    keyword arguments.
13. Fixture migrations in this commit:
    - `test_review.py:179`: the world's `models.toml` becomes
      `codex = "codex-m"\nclaude = "claude-m"\nopencode = "opencode-go/glm-5"\nagy = "gemini-m"\n`
      (`codex-m` and `claude-m` resolve through their fixed vendors; `oc-m` and `agy-m`
      would be unknown); `:406-407` assert the new shape:
      `check["authors"] == ["openai"]` and
      `[(m["slot"], [l["vendor"] for l in m["links"]]) for m in check["panel"]] == [("review", ["anthropic"]), ...]`
      with the judge's entry as the panel declares it.
    - `test_review.py:396`: the refusal label is now the slot, not the word "reviewer":
      `match=r"review reviewer-codex runs codex.*wrote commit"`.
    - `test_review.py:429` (`test_after_an_unconfined_write_a_commit_without_provenance_is_its_writers`):
      give the published writer `"links": [{"provider": "claude", "model": "", "switches": []}]`
      so the scenario stays a lab conflict (`claude` resolves to anthropic whatever its
      model), with `match="review reviewer runs claude"`; and add beside it
      `test_after_a_legacy_unconfined_write_the_review_is_refused`, the same writer
      without `links`, expecting `UsageError(match="predates model provenance")`.
    - `test_show.py:16, :670, :852, :862` and `test_reviews.py:13, :17`: import and build
      `LegacyVendorCheck` instead of `VendorCheck` (those fixtures are legacy documents
      and keep testing the legacy path); add new-shape cases beside them.

Export the new names in `__all__`.

- [ ] **Step 4: Run the tests and `make ci`** → green (mypy included).

- [ ] **Step 5: Commit** — `✨ feat(vendor): judge independence by the model's lab, judge included`

---

### Task 5: `ha show` and the documentation

**Files:**
- Modify: `src/headless_agents/show.py` (`vendors_line`, line ~458)
- Modify: `README.md` (vendor-rule paragraph ~line 676), `CHANGELOG.md` (`## Unreleased`)
- Test: `tests/unit/headless_agents/test_show.py`

**Interfaces:**
- Consumes: the `model-vendor` document shape of Task 4.
- Produces: nothing new for code; `vendors_line(check: Mapping[str, object]) -> str` keeps
  its signature.

- [ ] **Step 1: Write the failing test** (in `test_show.py`, beside the existing
  `vendors_line` tests, which stay unchanged for legacy documents):

```python
def test_vendors_line_renders_a_model_vendor_check() -> None:
    check = {
        "rule": "model-vendor",
        "table_digest": "sha256:" + "0" * 64,
        "authors": ["deepseek"],
        "commits": [{
            "sha": SHA_1, "run_id": RUN, "made_by": "engine", "providers": ["opencode"],
            "links": [{"provider": "opencode", "model": "opencode-go/deepseek-v4-pro",
                       "switches": [], "vendor": "deepseek"}],
        }],
        "panel": [
            {"slot": "review", "role": "r", "links": [
                {"provider": "opencode", "model": "anthropic/claude-x", "switches": [], "vendor": "anthropic"}]},
            {"slot": "judge", "role": "j", "links": [
                {"provider": "agy", "model": "gemini-x", "switches": [], "vendor": "google"}]},
        ],
    }
    assert vendors_line(check) == (
        "vendors authors deepseek (1 recorded commit) — "
        "panel review opencode anthropic, judge agy google: independent"
    )
```

- [ ] **Step 2: Run to see it fail** — `uv run pytest tests/unit/headless_agents/test_show.py -q` → FAIL on the new test only.

- [ ] **Step 3: Implement** — at the top of `vendors_line`:

```python
    if check.get("rule") == "model-vendor":
        return _model_vendor_line(check)
```

with `_model_vendor_line` reusing today's commit counting (`recorded`, `hand`,
`attributed` parts) and rendering the panel as
`", ".join(f"{m['slot']} {l['provider']} {l['vendor']}" for m in panel for l in m['links'])`,
reading every field defensively as `vendors_line` does today (a malformed entry is
skipped, never raised: `show` renders what it can).

`README.md`: replace "no reviewer shares a provider with whoever wrote a commit of the
range" by "no reviewer or judge runs a model from the lab that wrote a commit of the
range (vendors are resolved from the model, not the provider; see
`docs/specs/2026-10-13-model-vendor-rule-design.md`)", and add one paragraph on
`vendors.toml`:

```toml
# vendors.toml, beside models.toml: exact pairs for models the shipped table cannot name
[[pair]]
provider = "openai-compat"
model = "my-model"
vendor = "acme"
```

`CHANGELOG.md` `## Unreleased`: a `### Changed` entry, marked **breaking** for reviews:
the vendor rule compares the labs of the models (reviewers and judge) instead of
provider names; a model the table cannot name, or a `claude`/`codex` link whose
environment sets a backend URL, is refused; reviews of ranges holding commits recorded
before this change are refused until those commits leave the range; `vendor_check`
documents gain the `model-vendor` shape and legacy documents still read. An
`### Added` entry for `vendors.toml`.

- [ ] **Step 4: Run the full suite** — `make ci` → green; `rail check` → green.

- [ ] **Step 5: Commit** — `📝 docs(vendor): render and document the model-vendor rule`

---

## Execution notes

- Order is strict: 1 → 2 → 3 → 4 → 5 (each consumes the previous interfaces).
- After Task 5: `red-review`-style review of `git diff main...HEAD`, then PR, rail
  reviewer, independent non-Claude judge on the exact head (c2c46a2a), coordination go.
- No release, tag or version bump in this lot (operator decision).
