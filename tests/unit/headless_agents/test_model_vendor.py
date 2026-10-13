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
