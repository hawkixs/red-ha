"""One version across package metadata, the changelog, and install pins."""

from __future__ import annotations

import re
import tomllib
from importlib.metadata import version as installed_version
from pathlib import Path

import headless_agents

REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_ROOT = REPO_ROOT
PYPROJECT_PATH = PACKAGE_ROOT / "pyproject.toml"
CHANGELOG_PATH = PACKAGE_ROOT / "CHANGELOG.md"
README_PATH = PACKAGE_ROOT / "README.md"

_RELEASED_HEADING = re.compile(r"^## (\d+\.\d+\.\d+) — ", re.MULTILINE)
_INSTALL_PIN = re.compile(
    r'^uv (?:add|tool install) "headless-agents @ git\+https://github\.com/hawkixs/red-ha\.git@v(\d+\.\d+\.\d+)"$',
    re.MULTILINE,
)


def _package_version() -> str:
    with PYPROJECT_PATH.open("rb") as stream:
        return tomllib.load(stream)["project"]["version"]


def test_the_latest_released_changelog_section_is_the_package_version() -> None:
    text = CHANGELOG_PATH.read_text(encoding="utf-8")
    match = _RELEASED_HEADING.search(text)
    assert match, f"no '## X.Y.Z — ' heading found in {CHANGELOG_PATH}"
    assert match.group(1) == _package_version()


def test_no_unreleased_section_remains_for_the_package_version() -> None:
    version = _package_version()
    text = CHANGELOG_PATH.read_text(encoding="utf-8")
    pattern = re.compile(rf"^## Unreleased — {re.escape(version)}\b", re.MULTILINE)
    stray = pattern.findall(text)
    assert not stray, f"{CHANGELOG_PATH}: still has an 'Unreleased — {version}' section"


def test_every_install_pin_names_the_package_version_tag() -> None:
    version = _package_version()
    readme = README_PATH.read_text(encoding="utf-8")
    install_lines = [
        line for line in readme.splitlines() if line.startswith(("uv add ", "uv tool install "))
    ]
    versions_named = _INSTALL_PIN.findall(readme)
    assert len(install_lines) == len(versions_named) == 2, install_lines
    assert set(versions_named) == {version}, versions_named


def test_the_installed_package_matches_the_repository_version() -> None:
    assert Path(headless_agents.__file__).resolve().is_relative_to(PACKAGE_ROOT / "src")
    assert installed_version("headless-agents") == _package_version()
