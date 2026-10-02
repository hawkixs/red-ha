"""``ha init``: role and workflow presets with no model name (spec 0.5.4 §3.7)."""

from __future__ import annotations

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


def test_no_model_is_named() -> None:
    for _, text in presets.PRESET_FILES:
        assert all(not line.strip().startswith("model") for line in text.splitlines()), (
            "a preset names a model"
        )


def test_an_existing_file_is_never_overwritten(tmp_path: Path) -> None:
    (tmp_path / "workflows.toml").write_text("# mine\n")
    with pytest.raises(FileExistsError, match="workflows.toml"):
        presets.write_presets(tmp_path)
    assert not (tmp_path / "roles.toml").exists()
    assert (tmp_path / "workflows.toml").read_text() == "# mine\n"


def test_ha_init_print_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    home.mkdir()
    code = cli.main(["init", "--print"], environ={"HOME": str(home)})
    assert code == 0
    out = capsys.readouterr().out
    assert "[judge]" in out and "[review-codex]" in out
    assert not (home / ".config" / "ha" / "roles.toml").exists()


def test_ha_init_refuses_an_existing_file_with_exit_2(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".config" / "ha").mkdir(parents=True)
    (home / ".config" / "ha" / "roles.toml").write_text("")
    assert cli.main(["init"], environ={"HOME": str(home)}) == 2
