"""Which model each link gets, and where the declared defaults are read (spec 0.5.0 §3.1, §3.3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from headless_agents import cli_models, model_live
from headless_agents.cli_models import (
    ModelsError,
    check_known_model,
    models_for,
    resolve_models,
)


@pytest.mark.parametrize(
    ("link", "default", "role", "declared", "expected"),
    [
        (("codex", "own"), "flag", "role", {"codex": "file"}, ("own", "chain link")),
        (("codex", ""), "flag", "role", {"codex": "file"}, ("flag", "-m")),
        (("codex", ""), "", "role", {"codex": "file"}, ("role", "role")),
        (("codex", ""), "", "", {"codex": "file"}, ("file", "models.toml")),
        (("agy", ""), "", "", {}, ("", "rail default")),
    ],
)
def test_each_model_names_its_source(
    tmp_path: Path,
    link: tuple[str, str],
    default: str,
    role: str,
    declared: dict[str, str],
    expected: tuple[str, str],
) -> None:
    assert hasattr(cli_models, "resolve_model_sources")
    got = cli_models.resolve_model_sources(
        (link,),
        default=default,
        role_model=role,
        declared=declared,
        declared_path=tmp_path / "models.toml",
    )
    assert got[link[0]] == expected


def _models_file(home: Path, text: str) -> Path:
    directory = home / ".config" / "ha"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "models.toml"
    path.write_text(text)
    return path


def test_a_relative_xdg_config_home_falls_back_to_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _models_file(home, 'codex = "from-home"\n')
    got = models_for((("codex", ""),), default="", environ={"XDG_CONFIG_HOME": "rel"}, home=home)
    assert got == {"codex": "from-home"}


def test_a_models_file_linking_into_a_repository_is_refused(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".config" / "ha").mkdir(parents=True)
    planted = tmp_path / "repo" / "models.toml"
    planted.parent.mkdir()
    planted.write_text('codex = "planted"\n')
    (home / ".config" / "ha" / "models.toml").symlink_to(planted)
    with pytest.raises(ModelsError, match="outside the configuration directory"):
        models_for((("codex", ""),), default="", environ={}, home=home)


def test_the_role_model_sits_between_m_and_models_toml(tmp_path: Path) -> None:
    declared = {"codex": "from-file"}
    path = tmp_path / "models.toml"
    links = (("codex", ""),)
    assert resolve_models(
        links, default="", role_model="from-role", declared=declared, declared_path=path
    ) == {"codex": "from-role"}
    assert resolve_models(
        links, default="from-m", role_model="from-role", declared=declared, declared_path=path
    ) == {"codex": "from-m"}
    assert resolve_models(
        (("codex", "own"),),
        default="from-m",
        role_model="from-role",
        declared=declared,
        declared_path=path,
    ) == {"codex": "own"}
    assert resolve_models(
        links, default="", role_model="", declared=declared, declared_path=path
    ) == {"codex": "from-file"}


def test_a_role_model_spares_the_models_file(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _models_file(home, "this is not toml")
    got = models_for((("codex", ""),), default="", role_model="m", environ={}, home=home)
    assert got == {"codex": "m"}


CATALOGUE = """\
schema = 1

[codex."gpt-6-sol"]
purpose = "judgment"
tasks = [{kind = "code-review"}]
cost = {kind = "subscription"}
verified_at = 2026-10-01
source = "operator"
"""


def _catalogue_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / ".config" / "ha").mkdir(parents=True)
    (home / ".config" / "ha" / "catalog.toml").write_text(CATALOGUE)
    return home


def test_a_catalogued_model_passes(tmp_path: Path) -> None:
    home = _catalogue_home(tmp_path)
    check_known_model("codex", "gpt-6-sol", environ={"HOME": str(home)}, home=home)


def test_an_unknown_model_is_refused_with_close_ids(tmp_path: Path) -> None:
    home = _catalogue_home(tmp_path)
    with pytest.raises(ModelsError, match=r"gpt-6-sl.*catalog\.toml.*gpt-6-sol"):
        check_known_model("codex", "gpt-6-sl", environ={"HOME": str(home)}, home=home)


def test_a_model_in_the_live_list_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = _catalogue_home(tmp_path)
    (home / ".config" / "ha" / "catalog.toml").write_text(
        CATALOGUE.replace('codex."gpt-6-sol"', 'agy."gemini-x"')
    )
    monkeypatch.setattr(
        model_live,
        "live_models",
        lambda provider, **_: model_live.LiveList("available", ("gemini-y",), "ok"),
    )
    check_known_model("agy", "gemini-y", environ={"HOME": str(home)}, home=home)


def test_no_catalogue_checks_nothing(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    check_known_model("codex", "anything", environ={"HOME": str(home)}, home=home)


def test_a_provider_the_catalogue_does_not_cover_is_not_checked(tmp_path: Path) -> None:
    home = _catalogue_home(tmp_path)
    check_known_model("claude", "anything", environ={"HOME": str(home)}, home=home)
