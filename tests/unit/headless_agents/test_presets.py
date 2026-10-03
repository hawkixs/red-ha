"""``ha init``: role and workflow presets with no model name (spec 0.5.4 §3.7)."""

from __future__ import annotations

import re
import stat
from importlib.metadata import version as package_version
from pathlib import Path

import pytest

from headless_agents import cli, presets
from headless_agents.roles import load_roles
from headless_agents.workflows import load_workflows


def test_the_presets_load_through_the_real_validation(tmp_path: Path) -> None:
    written = presets.write_presets(tmp_path)
    assert [p.name for p in written] == ["roles.toml", "workflows.toml"]
    roles = load_roles(tmp_path / "roles.toml", mcp_profiles={})
    assert set(roles) == {
        "judge",
        "closure",
        "builder",
        "builder-deep",
        "pr-judge-agy",
        "reviewer-agy",
    }
    workflows = load_workflows(tmp_path / "workflows.toml", roles=roles)
    assert set(workflows) == {"build", "build-deep", "review-agy", "review-codex"}
    assert roles["judge"].timeout == 1800 and roles["builder-deep"].timeout == 3600


_MODEL_PLACEHOLDER = re.compile(r'^# model = "<[^<>"]+>"$')


def test_no_model_is_named() -> None:
    for name, text in presets.PRESET_FILES:
        for line in text.splitlines():
            # models.toml is a file name, not a model; every other mention must be a
            # commented angle-bracket placeholder, so a real model name fails here.
            if "model" in line.replace("models.toml", ""):
                assert _MODEL_PLACEHOLDER.match(line), f"{name} names a model: {line!r}"


def test_each_header_names_ha_init_and_the_package_version() -> None:
    version = package_version("headless-agents")
    for name, text in presets.PRESET_FILES:
        header = text.splitlines()[0]
        assert "`ha init`" in header and version in header, name


def test_the_files_written_are_the_files_printed_and_private(tmp_path: Path) -> None:
    written = presets.write_presets(tmp_path)
    for path, (_, text) in zip(written, presets.PRESET_FILES, strict=True):
        assert path.read_text() == text
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_an_existing_file_is_never_overwritten(tmp_path: Path) -> None:
    (tmp_path / "workflows.toml").write_text("# mine\n")
    with pytest.raises(FileExistsError, match="workflows.toml"):
        presets.write_presets(tmp_path)
    assert not (tmp_path / "roles.toml").exists()
    assert (tmp_path / "workflows.toml").read_text() == "# mine\n"


@pytest.mark.parametrize("target_exists", [True, False], ids=["symlink", "dangling-symlink"])
def test_a_symlink_in_the_way_counts_as_existing(tmp_path: Path, target_exists: bool) -> None:
    elsewhere = tmp_path / "elsewhere.toml"
    if target_exists:
        elsewhere.write_text("# mine\n")
    directory = tmp_path / "config"
    directory.mkdir()
    (directory / "roles.toml").symlink_to(elsewhere)
    with pytest.raises(FileExistsError, match="roles.toml"):
        presets.write_presets(directory)
    assert not (directory / "workflows.toml").exists()
    assert elsewhere.exists() is target_exists
    if target_exists:
        assert elsewhere.read_text() == "# mine\n"


def test_ha_init_print_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    home.mkdir()
    code = cli.main(["init", "--print"], environ={"HOME": str(home)})
    assert code == 0
    out = capsys.readouterr().out
    assert "[judge]" in out and "[review-codex]" in out
    assert not (home / ".config" / "ha" / "roles.toml").exists()


def test_ha_init_refuses_an_existing_file_with_exit_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    (home / ".config" / "ha").mkdir(parents=True)
    (home / ".config" / "ha" / "roles.toml").write_text("")
    assert cli.main(["init"], environ={"HOME": str(home)}) == 2
    assert "roles.toml" in capsys.readouterr().err
