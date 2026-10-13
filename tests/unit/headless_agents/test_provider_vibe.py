"""The ``vibe`` adapter (Mistral Vibe 2.26.0, ``vibe -p --output streaming``).

Every shape asserted here was measured on Vibe 2.26.0 on 2026-10-09; the
fixtures under ``fixtures/vibe`` are those recorded streams with their paths
rewritten (spec 2026-10-13-vibe-rail-design.md, Appendix A).
"""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import shutil
import signal
import stat
import subprocess
import time
import tomllib
from pathlib import Path

import pytest

from headless_agents.capability import (
    INVALID_USAGE_EXIT_CODE,
    PROVIDER_FALLBACK_EXIT_CODE,
    TIMEOUT_EXIT_CODE,
)
from headless_agents.profile import CapabilityProfile, McpServer, Workspace
from headless_agents.providers import vibe
from headless_agents.spec import RunSpec

FIXTURES = Path(__file__).parent / "fixtures" / "vibe"
KEY_ENV = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "MISTRAL_API_KEY": "test-key"}


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
            "enable_telemetry",
            "enable_auto_update",
            "enable_update_checks",
            "enable_connectors",
            "enable_notifications",
            "include_project_context",
            "include_commit_signature",
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
            "bash": {"permission": "always"},
            "write_file": {"permission": "never"},
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
            "vibe",
            "--workdir",
            str(tmp_path),
            "--agent",
            "accept-edits",
            "--enabled-tools",
            "read_file",
            "--enabled-tools",
            "write_file",
            "--max-turns",
            "50",
            "--output",
            "streaming",
            "-p",
        ]

    def test_never_passes_a_trust_or_approval_flag(self, tmp_path: Path) -> None:
        command = vibe.build_vibe_command(workdir=tmp_path, enabled=("read_file",), max_turns=5)
        for flag in (
            "--trust",
            "--yolo",
            "--auto-approve",
            "--smart-approve",
            "--add-dir",
            "--worktree",
            "--max-price",
            "--continue",
            "-c",
            "--resume",
        ):
            assert flag not in command

    def test_refuses_an_empty_tool_list(self, tmp_path: Path) -> None:
        # Measured (s11): no --enabled-tools at all enables EVERY tool.
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


def _effect(
    tool: str, status: str, code: str | None = None, entry_id: str = "e1"
) -> dict[str, object]:
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
        events = _write(
            tmp_path,
            {"type": "notice", "level": "error", "message": "rate limited"},
            _assistant("partial"),
        )
        assert "rate limited" in (vibe.stream_error(events) or "")

    def test_stream_error_on_an_empty_final_answer(self, tmp_path: Path) -> None:
        events = _write(tmp_path, _assistant("   "))
        assert "without a final answer" in (vibe.stream_error(events) or "")

    def test_stream_error_on_a_missing_stream(self, tmp_path: Path) -> None:
        assert vibe.stream_error(tmp_path / "absent.jsonl") is not None


class TestReport:
    def test_the_report_is_the_last_assistant_text(self, tmp_path: Path) -> None:
        events = _write(
            tmp_path,
            _assistant("first"),
            _effect("file_system.read_file", "completed"),
            _assistant("final answer"),
        )
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


class TestLargeFourStreams:
    """``mistral-large-4`` (spec amendment A2) adds ``reasoning`` entries to the stream:
    they are neither effects nor messages and change no verdict."""

    def test_a_read_with_reasoning_entries_is_valid(self, tmp_path: Path) -> None:
        events = _copy(tmp_path, "read-large4-reasoning.events.jsonl")
        assert vibe.stream_error(events) is None
        report = tmp_path / "report.log"
        vibe.extract_report(events, report)
        assert "MARKER-INSIDE-7f3a" in report.read_text()
        assert not vibe.write_tool_started(events)
        assert vibe.count_tools(events) == {"file_system.bash": 1, "file_system.read_file": 1}

    def test_a_write_with_reasoning_entries_is_a_write(self, tmp_path: Path) -> None:
        events = _copy(tmp_path, "write-large4-reasoning.events.jsonl")
        assert vibe.stream_error(events) is None
        assert vibe.write_tool_started(events)
        assert vibe.tool_call_completed(events)
        assert vibe.count_tools(events) == {"file_system.write_file": 2}


class TestCountTools:
    def test_counts_effects_by_tool_name(self, tmp_path: Path) -> None:
        counts = vibe.count_tools(_copy(tmp_path, "write-inside-outside.events.jsonl"))
        assert counts == {"file_system.write_file": 2}

    def test_deduplicates_by_entry_id(self, tmp_path: Path) -> None:
        events = _write(
            tmp_path,
            _effect("file_system.read_file", "completed", entry_id="a"),
            _effect("file_system.read_file", "completed", entry_id="a"),
        )
        assert vibe.count_tools(events) == {"file_system.read_file": 1}

    def test_none_when_an_effect_lacks_an_id_or_a_tool(self, tmp_path: Path) -> None:
        events = _write(
            tmp_path, {"type": "effect", "detail": {}, "state": {"status": "completed"}}
        )
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
    @pytest.mark.parametrize(
        "name", ["VIBE_ACTIVE_MODEL", "vibe_home", "VIBE_BYPASS_TOOL_PERMISSIONS"]
    )
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
            "PATH": "/bin",
            "LANG": "C.UTF-8",
            "VIBE_ACTIVE_MODEL": "devstral",
            "VIBE_BYPASS_TOOL_PERMISSIONS": "true",
            "OPENAI_API_KEY": "other",
            "UV_CACHE_DIR": "/cache",
        }
        profile = CapabilityProfile(
            environment_passthrough=("UV_CACHE_DIR", "VIBE_BYPASS_TOOL_PERMISSIONS")
        )
        child = vibe.child_environment(tmp_path, ambient, profile, api_key="secret")
        assert child == {
            "HOME": str(tmp_path),
            "VIBE_HOME": str(tmp_path / ".vibe"),
            "TMPDIR": str(tmp_path),
            "PATH": "/bin",
            "LANG": "C.UTF-8",
            "TERM": "dumb",
            "AI_AGENT": "vibe",
            "MISTRAL_API_KEY": "secret",
            "UV_CACHE_DIR": "/cache",
        }

    def test_a_passthrough_never_overrides_ha(self, tmp_path: Path) -> None:
        ambient = {"HOME": "/elsewhere", "MISTRAL_API_KEY": "ambient"}
        profile = CapabilityProfile(environment_passthrough=("HOME", "MISTRAL_API_KEY"))
        child = vibe.child_environment(tmp_path, ambient, profile, api_key="resolved")
        assert child["HOME"] == str(tmp_path)
        assert child["MISTRAL_API_KEY"] == "resolved"


def _fake_vibe(
    tmp_path: Path,
    *,
    stdout_file: Path | None = None,
    exit_code: int = 0,
    sleep_seconds: float = 0.0,
    record_dir: Path | None = None,
) -> Path:
    """A real executable standing in for vibe: it records its argv, environment, cwd
    and stdin, prints a recorded stream, then exits with ``exit_code``."""
    script = tmp_path / "bin" / "vibe"
    script.parent.mkdir(parents=True, exist_ok=True)
    lines = ["#!/usr/bin/env bash"]
    if record_dir is not None:
        q = shlex.quote(str(record_dir))
        lines += [
            f"mkdir -p {q}",
            f'printf "%s\\n" "$@" > {q}/argv',
            f"env > {q}/env",
            f"pwd > {q}/cwd",
            f"cat > {q}/stdin",
            f'cp "$VIBE_HOME/config.toml" {q}/config.toml',
        ]
    else:
        lines.append("cat > /dev/null")
    if stdout_file is not None:
        lines.append(f"cat {shlex.quote(str(stdout_file))}")
    if sleep_seconds:
        lines.append(f"sleep {sleep_seconds}")
    lines.append(f"exit {exit_code}")
    script.write_text("\n".join(lines) + "\n")
    script.chmod(0o755)
    return script


def _run(tmp_path: Path, script: Path, **overrides: object) -> int:
    logs = tmp_path / "logs"
    arguments: dict[str, object] = {
        "prompt": "do it",
        "name": "run",
        "model": "mistral-large-latest",
        "timeout_seconds": 30.0,
        "max_turns": 50,
        "events_log": logs / "events.jsonl",
        "report_log": logs / "report.log",
        "stderr_log": logs / "stderr.log",
        "profile": CapabilityProfile(),
        "environment": KEY_ENV,
        "executable": str(script),
        "ephemeral_root": tmp_path / "homes",
    }
    arguments.update(overrides)
    (tmp_path / "homes").mkdir(exist_ok=True)
    return vibe.run_vibe(**arguments)  # type: ignore[arg-type]


class TestRunVibe:
    def test_a_valid_read_returns_0_with_its_report(self, tmp_path: Path) -> None:
        record = tmp_path / "record"
        script = _fake_vibe(
            tmp_path, stdout_file=FIXTURES / "read-ok.events.jsonl", record_dir=record
        )
        assert _run(tmp_path, script) == 0
        assert (tmp_path / "logs" / "report.log").read_text().strip()
        argv = (record / "argv").read_text().splitlines()
        assert argv[-1] == "-p" and "--trust" not in argv
        assert (record / "stdin").read_text() == "do it"
        env = dict(
            line.split("=", 1) for line in (record / "env").read_text().splitlines() if "=" in line
        )
        assert env["MISTRAL_API_KEY"] == "test-key" and env["AI_AGENT"] == "vibe"
        assert not [k for k in env if k.startswith("VIBE_") and k != "VIBE_HOME"]
        assert tomllib.loads((record / "config.toml").read_text())["enable_connectors"] is False

    def test_the_cwd_is_the_ephemeral_home_and_it_is_removed(self, tmp_path: Path) -> None:
        record = tmp_path / "record"
        script = _fake_vibe(
            tmp_path, stdout_file=FIXTURES / "read-ok.events.jsonl", record_dir=record
        )
        _run(tmp_path, script)
        cwd = Path((record / "cwd").read_text().strip())
        assert cwd.parent.parent == tmp_path / "homes"
        assert not cwd.exists()

    def test_turn_limit_exit_zero_is_a_failure(self, tmp_path: Path) -> None:
        script = _fake_vibe(tmp_path, stdout_file=FIXTURES / "turn-limit.events.jsonl")
        assert _run(tmp_path, script) == PROVIDER_FALLBACK_EXIT_CODE
        assert "without a final answer" in (tmp_path / "logs" / "stderr.log").read_text()

    def test_a_zero_exit_with_bye_in_stdout_is_a_failure(self, tmp_path: Path) -> None:
        script = _fake_vibe(tmp_path, stdout_file=FIXTURES / "sigint-bye.stdout")
        assert _run(tmp_path, script) == PROVIDER_FALLBACK_EXIT_CODE

    def test_a_failure_after_a_completed_write_is_not_replayable(self, tmp_path: Path) -> None:
        (tmp_path / "ws").mkdir()
        workspace = Workspace(path=tmp_path / "ws", write=True)
        script = _fake_vibe(
            tmp_path, stdout_file=FIXTURES / "write-inside-outside.events.jsonl", exit_code=1
        )
        assert _run(tmp_path, script, workspace=workspace) == 1

    def test_a_failure_with_only_denied_writes_is_replayable(self, tmp_path: Path) -> None:
        (tmp_path / "ws").mkdir()
        workspace = Workspace(path=tmp_path / "ws", write=True)
        script = _fake_vibe(
            tmp_path, stdout_file=FIXTURES / "denied-by-policy.events.jsonl", exit_code=1
        )
        assert _run(tmp_path, script, workspace=workspace) == PROVIDER_FALLBACK_EXIT_CODE

    def test_killed_by_a_signal_in_a_writable_workspace_is_not_replayable(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "ws").mkdir()
        workspace = Workspace(path=tmp_path / "ws", write=True)
        script = tmp_path / "bin" / "vibe"
        script.parent.mkdir(parents=True)
        script.write_text("#!/usr/bin/env bash\ncat > /dev/null\nkill -TERM $$\n")
        script.chmod(0o755)
        assert _run(tmp_path, script, workspace=workspace) == 1

    def test_deadline_read_only_with_an_empty_stream_is_124(self, tmp_path: Path) -> None:
        # Operator ruling 2026-10-13: never 4 on vibe (completion-only stream).
        script = _fake_vibe(tmp_path, sleep_seconds=30)
        start = time.monotonic()
        assert _run(tmp_path, script, timeout_seconds=1.0) == TIMEOUT_EXIT_CODE
        assert time.monotonic() - start < 15

    def test_deadline_after_a_read_effect_is_124(self, tmp_path: Path) -> None:
        script = _fake_vibe(
            tmp_path, stdout_file=FIXTURES / "read-ok.events.jsonl", sleep_seconds=30
        )
        assert _run(tmp_path, script, timeout_seconds=1.0) == TIMEOUT_EXIT_CODE

    def test_deadline_with_an_unreadable_stream_is_124(self, tmp_path: Path) -> None:
        garbage = tmp_path / "garbage.out"
        garbage.write_text("not json\n")
        script = _fake_vibe(tmp_path, stdout_file=garbage, sleep_seconds=30)
        assert _run(tmp_path, script, timeout_seconds=1.0) == TIMEOUT_EXIT_CODE

    def test_deadline_through_a_fake_popen_terminates_the_group(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []

        class _Process:
            pid = 999_999
            returncode = None

            def communicate(self, input: str | None = None, timeout: float | None = None) -> None:
                calls.append("communicate")
                raise subprocess.TimeoutExpired(cmd="vibe", timeout=timeout or 0)

        class _Lifeline:
            def release(self) -> None:
                calls.append("released")

        monkeypatch.setattr(vibe, "spawn_watched", lambda command, **kw: (_Process(), _Lifeline()))
        monkeypatch.setattr(
            vibe, "terminate_process_group", lambda process: calls.append("terminated")
        )
        assert _run(tmp_path, tmp_path / "unused") == TIMEOUT_EXIT_CODE
        assert calls == ["communicate", "terminated", "released"]

    def test_deadline_in_a_writable_workspace_is_124(self, tmp_path: Path) -> None:
        (tmp_path / "ws").mkdir()
        workspace = Workspace(path=tmp_path / "ws", write=True)
        script = _fake_vibe(tmp_path, sleep_seconds=30)
        assert _run(tmp_path, script, timeout_seconds=1.0, workspace=workspace) == TIMEOUT_EXIT_CODE

    def test_an_expired_caller_deadline_launches_nothing(self, tmp_path: Path) -> None:
        record = tmp_path / "record"
        script = _fake_vibe(tmp_path, record_dir=record)
        assert _run(tmp_path, script, deadline=time.monotonic() - 1) == TIMEOUT_EXIT_CODE
        assert not record.exists()

    def test_a_missing_key_is_refused_with_2(self, tmp_path: Path) -> None:
        record = tmp_path / "record"
        script = _fake_vibe(tmp_path, record_dir=record)
        env = {"PATH": KEY_ENV["PATH"], "HOME": str(tmp_path / "nohome")}
        assert _run(tmp_path, script, environment=env) == INVALID_USAGE_EXIT_CODE
        assert not record.exists()
        assert "keys.toml" in (tmp_path / "logs" / "stderr.log").read_text()

    def test_a_vibe_passthrough_is_refused_with_2(self, tmp_path: Path) -> None:
        script = _fake_vibe(tmp_path)
        profile = CapabilityProfile(environment_passthrough=("VIBE_ACTIVE_MODEL",))
        assert _run(tmp_path, script, profile=profile) == INVALID_USAGE_EXIT_CODE

    def test_an_invalid_model_is_refused_with_2(self, tmp_path: Path) -> None:
        record = tmp_path / "record"
        script = _fake_vibe(tmp_path, record_dir=record)
        assert _run(tmp_path, script, model='x"\n[tools.bash]') == INVALID_USAGE_EXIT_CODE
        assert not record.exists()

    def test_mcp_profile_is_refused_with_2(self, tmp_path: Path) -> None:
        script = _fake_vibe(tmp_path)
        profile = CapabilityProfile(mcp=McpServer(name="brain", url="http://127.0.0.1:8765/mcp"))
        assert _run(tmp_path, script, profile=profile) == INVALID_USAGE_EXIT_CODE
        assert "MCP" in (tmp_path / "logs" / "stderr.log").read_text()

    @pytest.mark.parametrize("name", ["api-error-invalid-key", "api-error-invalid-model"])
    def test_an_api_error_before_any_effect_is_replayable(self, tmp_path: Path, name: str) -> None:
        # Recorded by Task 0 M1: exit 1, stdout holds only the user message entry.
        exit_codes = {"api-error-invalid-key": 1, "api-error-invalid-model": 1}
        script = _fake_vibe(
            tmp_path, stdout_file=FIXTURES / f"{name}.events.jsonl", exit_code=exit_codes[name]
        )
        assert _run(tmp_path, script) == PROVIDER_FALLBACK_EXIT_CODE

    def test_a_missing_executable_is_replayable(self, tmp_path: Path) -> None:
        assert _run(tmp_path, tmp_path / "absent-vibe") == PROVIDER_FALLBACK_EXIT_CODE

    def test_the_home_is_refused_under_the_workspace(self, tmp_path: Path) -> None:
        record = tmp_path / "record"
        workspace = Workspace(path=tmp_path)
        script = _fake_vibe(tmp_path, record_dir=record)
        code = _run(tmp_path, script, workspace=workspace, ephemeral_root=tmp_path / "homes")
        assert code == INVALID_USAGE_EXIT_CODE
        assert not record.exists()
        assert list((tmp_path / "homes").iterdir()) == []

    def test_a_descendant_does_not_outlive_the_deadline(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "child.pid"
        script = tmp_path / "bin" / "vibe"
        script.parent.mkdir(parents=True)
        script.write_text(
            "#!/usr/bin/env bash\ncat > /dev/null\n"
            f"sleep 60 & echo $! > {shlex.quote(str(pid_file))}\nwait\n"
        )
        script.chmod(0o755)
        assert _run(tmp_path, script, timeout_seconds=1.0) == TIMEOUT_EXIT_CODE
        child = int(pid_file.read_text())

        def alive(pid: int) -> bool:
            try:
                state = Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[0]
            except OSError:
                return False
            # a zombie has stopped running
            return state != "Z"

        # terminate_process_group returns only once the group is gone or SIGKILLed:
        # the descendant must already be stopped (gone, or a zombie) at return.
        assert not alive(child), "a descendant of vibe survived run_vibe's return"

    def test_an_interrupt_kills_the_group_and_releases_the_lifeline(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events: list[str] = []
        group_checks: list[tuple[str, bool]] = []
        spawned: list[subprocess.Popen[str]] = []
        real_spawn = vibe.spawn_watched

        class _Lifeline:
            def __init__(self, inner: object) -> None:
                self._inner = inner

            def release(self) -> None:
                events.append("released")
                self._inner.release()  # type: ignore[attr-defined]

        def spawn(command: list[str], **kwargs: object):  # type: ignore[no-untyped-def]
            process, lifeline = real_spawn(command, **kwargs)
            spawned.append(process)

            def communicate(*args: object, **kw: object) -> None:
                raise KeyboardInterrupt

            monkeypatch.setattr(process, "communicate", communicate)
            return process, _Lifeline(lifeline)

        real_terminate = vibe.terminate_process_group

        def _group_gone(pid: int) -> bool:
            try:
                os.killpg(pid, 0)
            except ProcessLookupError:
                return True
            return False

        def terminate(process: subprocess.Popen[str]) -> None:
            # really kills the group
            real_terminate(process)
            group_checks.append(("group_gone", _group_gone(process.pid)))
            events.append("terminated")

        monkeypatch.setattr(vibe, "spawn_watched", spawn)
        monkeypatch.setattr(vibe, "terminate_process_group", terminate)
        script = _fake_vibe(tmp_path, sleep_seconds=30)
        try:
            with pytest.raises(KeyboardInterrupt):
                _run(tmp_path, script)
        finally:
            for process in spawned:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        assert group_checks == [("group_gone", True)]
        assert events == ["terminated", "released"]


class TestProvider:
    def _spec(self, tmp_path: Path, script: Path, **overrides: object) -> RunSpec:
        values: dict[str, object] = {
            "prompt": "do it",
            "name": "ha-test",
            "model": "mistral-large-latest",
            "profile": CapabilityProfile(),
            "timeout_seconds": 30.0,
            "max_turns": 50,
            "run_dir": tmp_path / "run",
            "executable": str(script),
            "environment": KEY_ENV,
        }
        values.update(overrides)
        return RunSpec(**values)  # type: ignore[arg-type]

    def test_run_records_a_result_without_usage(self, tmp_path: Path) -> None:
        (tmp_path / "homes").mkdir()
        script = _fake_vibe(tmp_path, stdout_file=FIXTURES / "read-ok.events.jsonl")
        provider = vibe.VibeProvider(ephemeral_root=tmp_path / "homes")
        result = provider.run(self._spec(tmp_path, script))
        assert result.exit_code == 0 and result.provider == "vibe"
        assert result.model == "mistral-large-latest"
        assert result.tokens is None and result.cost_usd is None and result.model_reported is None
        assert result.text
        assert (tmp_path / "run" / "result.json").is_file()

    def test_build_command_previews_the_real_command(self, tmp_path: Path) -> None:
        provider = vibe.VibeProvider(ephemeral_root=tmp_path / "homes")
        command = provider.build_command(self._spec(tmp_path, Path("/opt/vibe")))
        assert command[0] == "/opt/vibe" and command[-1] == "-p"
        assert command[command.index("--workdir") + 1].endswith("/workdir")

    def test_a_schema_is_refused(self, tmp_path: Path) -> None:
        from headless_agents.structured import SchemaError

        provider = vibe.VibeProvider(ephemeral_root=tmp_path / "homes")
        spec = self._spec(tmp_path, Path("/opt/vibe"), output_schema={"type": "object"})
        with pytest.raises(SchemaError):
            provider.run(spec)
