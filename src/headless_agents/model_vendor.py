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
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from .roles import Role

UNKNOWN: Final = "unknown"
VENDORS_FILE_NAME: Final = "vendors.toml"
_GRAMMARS: Final = frozenset({"org", "bare", "opencode"})
_OPENCODE_GRAMMARS: Final = frozenset({"org", "bare"})
ENV_SWITCHES: Final[Mapping[str, frozenset[str]]] = {
    "codex": frozenset({"OPENAI_BASE_URL", "OPENAI_API_BASE"}),
    "claude": frozenset({"ANTHROPIC_BASE_URL"}),
}


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
            not isinstance(provider, str)
            or not provider
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
        LinkRecord(
            link.provider,
            models.get(link.provider, ""),
            backend_switches(link.provider, environment),
        )
        for link in role.links
    )


_CODEX_FLAGS: Final = {
    "--profile": "--profile",
    "-p": "--profile",
    "--oss": "--oss",
    "--local-provider": "--local-provider",
}


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
            return next((vendor for glob, vendor in self.bare if fnmatchcase(model, glob)), UNKNOWN)
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
            "providers": {key: dict(value) for key, value in sorted(self.providers.items())},
            "orgs": dict(sorted(self.orgs.items())),
            "bare": [list(item) for item in self.bare],
            "opencode": {key: dict(value) for key, value in sorted(self.opencode.items())},
            "pairs": sorted(
                [provider, model, vendor] for (provider, model), vendor in self.pairs.items()
            ),
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
        providers={
            key: _entry(value, _GRAMMARS, f"{where} [providers] {key}")
            for key, value in table("providers").items()
        },
        orgs={key: _text(value, f"{where} [orgs] {key}") for key, value in table("orgs").items()},
        bare=tuple(
            (key, _text(value, f"{where} [bare] {key}")) for key, value in table("bare").items()
        ),
        opencode={
            key: _entry(value, _OPENCODE_GRAMMARS, f"{where} [opencode] {key}")
            for key, value in table("opencode").items()
        },
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
    "ENV_SWITCHES",
    "LinkRecord",
    "UNKNOWN",
    "VENDORS_FILE_NAME",
    "VendorTable",
    "VendorTableError",
    "backend_switches",
    "codex_argv_switches",
    "load_table",
    "role_links",
    "shipped_table",
]
