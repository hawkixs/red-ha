"""The ``vibe`` adapter (Mistral Vibe 2.26.0, ``vibe -p --output streaming``).

Every shape asserted here was measured on Vibe 2.26.0 on 2026-10-09; the
fixtures under ``fixtures/vibe`` are those recorded streams with their paths
rewritten (spec 2026-10-13-vibe-rail-design.md, Appendix A).
"""

from __future__ import annotations

import json
import shutil
import stat
import tomllib
from pathlib import Path

import pytest

from headless_agents.profile import CapabilityProfile, Workspace
from headless_agents.providers import vibe

FIXTURES = Path(__file__).parent / "fixtures" / "vibe"


class TestValidateModel:
    @pytest.mark.parametrize(
        "model", ["mistral-large-latest", "devstral-medium-2507", "ft:open-mistral-7b:abc123"]
    )
    def test_accepts_a_plain_identifier(self, model: str) -> None:
        assert vibe.validate_model(model) == model

    @pytest.mark.parametrize(
        "model", ["", 'a"b', "a\nb", "a]b", "a b", "é", "-leading-dash", "a" * 129]
    )
    def test_refuses_anything_else(self, model: str) -> None:
        with pytest.raises(ValueError, match="model id"):
            vibe.validate_model(model)


class TestToolLists:
    def test_no_workspace_reads_only_its_private_directory(self) -> None:
        enabled, permissions = vibe.tool_lists(None)
        assert enabled == ("read_file",)
        assert permissions == {"write_file": "never", "edit": "never", "bash": "never"}

    def test_read_only_workspace(self, tmp_path: Path) -> None:
        enabled, permissions = vibe.tool_lists(Workspace(path=tmp_path))
        assert enabled == vibe.READ_TOOLS
        assert permissions == {"write_file": "never", "edit": "never", "bash": "never"}

    def test_writable_workspace_without_shell(self, tmp_path: Path) -> None:
        enabled, permissions = vibe.tool_lists(Workspace(path=tmp_path, write=True))
        assert enabled == (*vibe.READ_TOOLS, "write_file", "edit")
        assert permissions == {"write_file": "always", "edit": "always", "bash": "never"}

    def test_writable_workspace_with_shell(self, tmp_path: Path) -> None:
        enabled, permissions = vibe.tool_lists(Workspace(path=tmp_path, write=True, shell=True))
        assert enabled == (*vibe.READ_TOOLS, "write_file", "edit", "bash")
        assert permissions == {"write_file": "always", "edit": "always", "bash": "always"}


class TestVibeConfig:
    def _parsed(self, **permissions: str) -> dict[str, object]:
        text = vibe.vibe_config(model="mistral-large-latest", permissions=permissions)
        return tomllib.loads(text)

    def test_pins_the_model_and_its_alias(self) -> None:
        config = self._parsed()
        assert config["active_model"] == "mistral-large-latest"
        assert config["models"] == [
            {"name": "mistral-large-latest", "provider": "mistral", "alias": "mistral-large-latest"}
        ]

    def test_turns_every_side_channel_off(self) -> None:
        config = self._parsed()
        for key in (
            "enable_telemetry", "enable_auto_update", "enable_update_checks", "enable_connectors",
            "enable_notifications", "include_project_context", "include_commit_signature",
        ):
            assert config[key] is False, key
        assert config["session_logging"] == {"enabled": False, "generate_titles": False}
        assert config["experiments"] == {"enable": False}

    def test_points_every_side_host_at_a_closed_loopback_port(self) -> None:
        config = self._parsed()
        for key in ("vibe_base_url", "vibe_code_sessions_base_url", "console_base_url"):
            assert config[key] == "http://127.0.0.1:9", key

    def test_writes_each_tool_permission(self) -> None:
        config = self._parsed(write_file="never", bash="always")
        assert config["tools"] == {
            "bash": {"permission": "always"}, "write_file": {"permission": "never"},
        }

    def test_refuses_an_invalid_model_before_writing_anything(self) -> None:
        with pytest.raises(ValueError, match="model id"):
            vibe.vibe_config(model='x"\n[tools.bash]', permissions={})

    def test_the_text_round_trips_to_the_same_document(self) -> None:
        text = vibe.vibe_config(model="mistral-large-latest", permissions={"bash": "never"})
        assert vibe.vibe_config(model="mistral-large-latest", permissions={"bash": "never"}) == text
        assert tomllib.loads(text)["tools"] == {"bash": {"permission": "never"}}

    def test_refuses_an_unknown_permission(self) -> None:
        with pytest.raises(ValueError, match="permission"):
            vibe.vibe_config(model="m", permissions={"bash": "ask"})


class TestBuildCommand:
    def test_the_measured_programmatic_command(self, tmp_path: Path) -> None:
        command = vibe.build_vibe_command(
            workdir=tmp_path, enabled=("read_file", "write_file"), max_turns=50
        )
        assert command == [
            "vibe", "--workdir", str(tmp_path), "--agent", "accept-edits", "--enabled-tools",
            "read_file", "--enabled-tools", "write_file", "--max-turns", "50", "--output",
            "streaming", "-p",
        ]

    def test_never_passes_a_trust_or_approval_flag(self, tmp_path: Path) -> None:
        command = vibe.build_vibe_command(workdir=tmp_path, enabled=("read_file",), max_turns=5)
        for flag in (
            "--trust", "--yolo", "--auto-approve", "--smart-approve", "--add-dir", "--worktree",
            "--max-price", "--continue", "-c", "--resume",
        ):
            assert flag not in command

    def test_refuses_an_empty_tool_list(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="at least one tool"):
            vibe.build_vibe_command(workdir=tmp_path, enabled=(), max_turns=5)

    def test_refuses_a_non_positive_turn_budget(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="max_turns"):
            vibe.build_vibe_command(workdir=tmp_path, enabled=("read_file",), max_turns=0)

    def test_uses_the_given_executable(self, tmp_path: Path) -> None:
        command = vibe.build_vibe_command(
            workdir=tmp_path, enabled=("read_file",), max_turns=5, executable="/opt/vibe"
        )
        assert command[0] == "/opt/vibe"


def _copy(tmp_path: Path, name: str) -> Path:
    target = tmp_path / "events.jsonl"
    shutil.copy(FIXTURES / name, target)
    return target


def _write(tmp_path: Path, *entries: dict[str, object]) -> Path:
    target = tmp_path / "events.jsonl"
    target.write_text("".join(json.dumps(entry) + "\n" for entry in entries))
    return target


def _assistant(text: str) -> dict[str, object]:
    return {"type": "message", "role": "assistant", "content": [{"type": "text", "text": text}]}


def _effect(tool: str, status: str, code: str | None = None, entry_id: str = "e1") -> dict[str, object]:
    state: dict[str, object] = {"status": status}
    if code is not None:
        state["error"] = {"code": code, "message": "x", "details": None}
    return {"id": entry_id, "type": "effect", "detail": {"toolName": tool}, "state": state}


class TestStreamValidity:
    def test_a_recorded_read_is_valid(self, tmp_path: Path) -> None:
        assert vibe.stream_error(_copy(tmp_path, "read-ok.events.jsonl")) is None

    def test_stream_error_on_sigint_bye_line(self, tmp_path: Path) -> None:
        events = _copy(tmp_path, "sigint-bye.stdout")
        assert "not JSON lines" in (vibe.stream_error(events) or "")

    def test_stream_error_on_turn_limit_without_final_answer(self, tmp_path: Path) -> None:
        events = _copy(tmp_path, "turn-limit.events.jsonl")
        assert "without a final answer" in (vibe.stream_error(events) or "")

    def test_stream_error_on_a_half_written_last_line(self, tmp_path: Path) -> None:
        events = tmp_path / "events.jsonl"
        events.write_text(json.dumps(_assistant("ok")) + "\n" + '{"type": "mess')
        assert "not JSON lines" in (vibe.stream_error(events) or "")

    def test_stream_error_on_an_error_notice(self, tmp_path: Path) -> None:
        events = _write(tmp_path, {"type": "notice", "level": "error", "message": "rate limited"}, _assistant("partial"))
        assert "rate limited" in (vibe.stream_error(events) or "")

    def test_stream_error_on_an_empty_final_answer(self, tmp_path: Path) -> None:
        events = _write(tmp_path, _assistant("   "))
        assert "without a final answer" in (vibe.stream_error(events) or "")

    def test_stream_error_on_a_missing_stream(self, tmp_path: Path) -> None:
        assert vibe.stream_error(tmp_path / "absent.jsonl") is not None


class TestReport:
    def test_the_report_is_the_last_assistant_text(self, tmp_path: Path) -> None:
        events = _write(tmp_path, _assistant("first"), _effect("file_system.read_file", "completed"), _assistant("final answer"))
        report = tmp_path / "report.log"
        vibe.extract_report(events, report)
        assert report.read_text() == "final answer"

    def test_a_stream_with_no_answer_leaves_an_empty_report(self, tmp_path: Path) -> None:
        report = tmp_path / "report.log"
        vibe.extract_report(_copy(tmp_path, "turn-limit.events.jsonl"), report)
        assert report.read_text() == ""

    def test_an_unreadable_stream_keeps_the_last_readable_answer(self, tmp_path: Path) -> None:
        events = tmp_path / "events.jsonl"
        events.write_text(json.dumps(_assistant("kept")) + "\nBye!\n")
        report = tmp_path / "report.log"
        vibe.extract_report(events, report)
        assert report.read_text() == "kept"


class TestPredicates:
    def test_nothing_started_on_a_message_only_stream(self, tmp_path: Path) -> None:
        events = _write(tmp_path, _assistant("hi"))
        assert not vibe.tool_call_started(events)
        assert not vibe.write_tool_started(events)
        assert not vibe.tool_call_completed(events)

    def test_a_read_started_but_wrote_nothing(self, tmp_path: Path) -> None:
        events = _copy(tmp_path, "read-ok.events.jsonl")
        assert vibe.tool_call_started(events)
        assert not vibe.write_tool_started(events)
        assert not vibe.tool_call_completed(events)

    def test_a_completed_write_is_a_write(self, tmp_path: Path) -> None:
        events = _copy(tmp_path, "write-inside-outside.events.jsonl")
        assert vibe.write_tool_started(events)
        assert vibe.tool_call_completed(events)

    def test_denied_writes_prove_nothing_ran(self, tmp_path: Path) -> None:
        events = _copy(tmp_path, "denied-by-policy.events.jsonl")
        assert vibe.tool_call_started(events)
        assert not vibe.write_tool_started(events)
        assert not vibe.tool_call_completed(events)

    def test_an_unknown_tool_counts_as_a_write(self, tmp_path: Path) -> None:
        events = _write(tmp_path, _effect("process.start", "failed", code="tool_failed"))
        assert vibe.write_tool_started(events)

    def test_fail_closed_on_an_unreadable_stream(self, tmp_path: Path) -> None:
        events = tmp_path / "events.jsonl"
        events.write_text("not json\n")
        assert vibe.tool_call_started(events)
        assert vibe.write_tool_started(events)
        assert not vibe.tool_call_completed(events)

    def test_fail_closed_on_a_missing_stream(self, tmp_path: Path) -> None:
        absent = tmp_path / "absent.jsonl"
        assert vibe.tool_call_started(absent)
        assert vibe.write_tool_started(absent)


class TestCountTools:
    def test_counts_effects_by_tool_name(self, tmp_path: Path) -> None:
        counts = vibe.count_tools(_copy(tmp_path, "write-inside-outside.events.jsonl"))
        assert counts == {"file_system.write_file": 2}

    def test_deduplicates_by_entry_id(self, tmp_path: Path) -> None:
        events = _write(tmp_path, _effect("file_system.read_file", "completed", entry_id="a"), _effect("file_system.read_file", "completed", entry_id="a"))
        assert vibe.count_tools(events) == {"file_system.read_file": 1}

    def test_none_when_an_effect_lacks_an_id_or_a_tool(self, tmp_path: Path) -> None:
        events = _write(tmp_path, {"type": "effect", "detail": {}, "state": {"status": "completed"}})
        assert vibe.count_tools(events) is None

    def test_none_when_unreadable_or_absent(self, tmp_path: Path) -> None:
        assert vibe.count_tools(None) is None
        assert vibe.count_tools(_copy(tmp_path, "sigint-bye.stdout")) is None


class TestHome:
    def test_builds_a_private_vibe_home_with_the_config(self, tmp_path: Path) -> None:
        home = vibe.build_vibe_home(root=tmp_path, name="run", config_text="x = 1\n")
        config = home / ".vibe" / "config.toml"
        assert config.read_text() == "x = 1\n"
        assert stat.S_IMODE(config.stat().st_mode) == 0o600
        assert stat.S_IMODE((home / ".vibe").stat().st_mode) == 0o700
        assert stat.S_IMODE(home.stat().st_mode) == 0o700
        assert vibe.private_workdir(home).is_dir()
        assert list(vibe.private_workdir(home).iterdir()) == []


class TestPassthrough:
    @pytest.mark.parametrize("name", ["VIBE_ACTIVE_MODEL", "vibe_home", "VIBE_BYPASS_TOOL_PERMISSIONS"])
    def test_a_vibe_variable_is_refused(self, name: str) -> None:
        profile = CapabilityProfile(environment_passthrough=(name,))
        assert "VIBE_" in (vibe.passthrough_refusal(profile) or "").upper()

    def test_other_variables_pass(self) -> None:
        profile = CapabilityProfile(environment_passthrough=("UV_CACHE_DIR",))
        assert vibe.passthrough_refusal(profile) is None


class TestApiKey:
    def test_the_environment_key_wins(self, tmp_path: Path) -> None:
        key, reason = vibe.resolve_api_key({"MISTRAL_API_KEY": "k", "HOME": str(tmp_path)})
        assert (key, reason) == ("k", None)

    def test_missing_key_names_the_keys_file_never_a_value(self, tmp_path: Path) -> None:
        key, reason = vibe.resolve_api_key({"HOME": str(tmp_path)})
        assert key is None
        assert "keys.toml" in (reason or "") and "MISTRAL_API_KEY" in (reason or "")

    def test_a_broken_declaration_is_a_reason_not_an_exception(self, tmp_path: Path) -> None:
        config = tmp_path / ".config" / "ha"
        config.mkdir(parents=True)
        (config / "keys.toml").write_text('mistral = "relative/path"\n')
        (config / "keys.toml").chmod(0o600)
        key, reason = vibe.resolve_api_key({"HOME": str(tmp_path)})
        assert key is None and "keys.toml" in (reason or "")


class TestChildEnvironment:
    def test_an_allowlist_with_the_key_and_the_marker(self, tmp_path: Path) -> None:
        ambient = {
            "PATH": "/bin", "LANG": "C.UTF-8", "VIBE_ACTIVE_MODEL": "devstral",
            "VIBE_BYPASS_TOOL_PERMISSIONS": "true", "OPENAI_API_KEY": "other", "UV_CACHE_DIR": "/cache",
        }
        profile = CapabilityProfile(environment_passthrough=("UV_CACHE_DIR", "VIBE_BYPASS_TOOL_PERMISSIONS"))
        child = vibe.child_environment(tmp_path, ambient, profile, api_key="secret")
        assert child == {
            "HOME": str(tmp_path), "VIBE_HOME": str(tmp_path / ".vibe"), "TMPDIR": str(tmp_path),
            "PATH": "/bin", "LANG": "C.UTF-8", "TERM": "dumb", "AI_AGENT": "vibe",
            "MISTRAL_API_KEY": "secret", "UV_CACHE_DIR": "/cache",
        }

    def test_a_passthrough_never_overrides_ha(self, tmp_path: Path) -> None:
        ambient = {"HOME": "/elsewhere", "MISTRAL_API_KEY": "ambient"}
        profile = CapabilityProfile(environment_passthrough=("HOME", "MISTRAL_API_KEY"))
        child = vibe.child_environment(tmp_path, ambient, profile, api_key="resolved")
        assert child["HOME"] == str(tmp_path)
        assert child["MISTRAL_API_KEY"] == "resolved"
