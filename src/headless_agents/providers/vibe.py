"""An isolated ``vibe`` adapter (Mistral Vibe, ``vibe -p --output streaming``).

Everything below was measured on Vibe 2.26.0 on 2026-10-09 (spec
2026-10-13-vibe-rail-design.md, Appendix A), not read from its documentation.

THE GATE IS VIBE'S OWN. A tool left out of ``--enabled-tools`` is still shown
to the model, but every call to it fails ``tool_denied`` ("approval policy")
and nothing runs; a call that needs an approval -- a path outside
``--workdir``, a workspace symlink that resolves outside it, ``bash`` under
``accept-edits`` -- is denied at once in ``-p`` mode ("approval callback").
The configuration repeats each write and shell decision as an explicit
``[tools.<name>] permission`` so the profile never depends on an agent's
defaults. ``--enabled-tools`` with NO value enables every tool: the list is
never empty. The code-mode meta-tools ``run_typescript`` and
``search_tool_functions`` cannot be disabled; their file effects pass the
same gate and ``run_typescript`` has no fetch, fs or Deno.

THE HOME IS EPHEMERAL AND THE CONFIGURATION IS HA'S. ``VIBE_HOME`` lives in
the run's ephemeral HOME with a ``config.toml`` that turns connectors,
telemetry, experiments, update checks, session journals and title
generation off and points the three side hosts at a closed loopback port:
measured, the only peer left is the model API. The repository's own
``.vibe/config.toml`` and ``AGENTS.md`` are ignored because ``--trust`` is
never passed. ``VIBE_*`` environment variables override any configuration
key, so none is inherited.

THE EXIT CODE PROVES NOTHING. An exhausted ``--max-turns`` exits 0 with no
final answer; SIGINT exits 0 and writes "Bye!" into stdout. Validity is read
from the stream (:func:`stream_error`), and ha only ever sends SIGTERM.
The final answer can claim effects the gate denied: tool evidence comes from
``effect`` entries only. The stream carries no usage, cost or model.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
import tomllib
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

from ..capability import (
    AGENT_MARKER_VARIABLE,
    INVALID_USAGE_EXIT_CODE,
    PROVIDER_FALLBACK_EXIT_CODE,
    TIMEOUT_EXIT_CODE,
    failure_code_after_a_write,
    terminate_process_group,
)
from ..event_log import read_events
from ..keys import KeysError, preset_key
from ..procgroup import preexec_for, spawn_watched
from ..profile import CapabilityProfile, Workspace
from ..result import RunResult
from ..run_record import answer_text, record, run_id_of
from ..sandbox import ephemeral_root as default_ephemeral_root
from ..sandbox import refuse_home_under_workspace
from ..spec import RunSpec
from ..structured import refuse_unsupported
from ..workspace import (
    armed_run,
    prepend,
    rail_preamble,
    settle_run,
    workspace_of,
    workspace_summary,
)

SUPPORTED_VERSION: Final = "2.26.0"
CLOSED_LOOPBACK: Final = "http://127.0.0.1:9"
#: Catalogue names a read-only workspace enables. No grep: the default runtime has
#: none (measured 2026-10-13, Task 0 M2: the model fell back to bash, denied).
READ_TOOLS: Final[tuple[str, ...]] = ("read_file",)
WRITE_TOOLS: Final = ("write_file", "edit")
SHELL_TOOL: Final = "bash"
_PERMISSIONS: Final = frozenset({"always", "never"})
_MODEL_ID: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")
_KEY_VARIABLE: Final = "MISTRAL_API_KEY"


def validate_model(model: str) -> str:
    """Return a plain model identifier, refusing values unsafe in TOML."""
    if not _MODEL_ID.fullmatch(model):
        raise ValueError(
            f"vibe: model id {model!r} is not a plain identifier (letters, digits and ._:/- only, at most 128 characters)"
        )
    return model


def tool_lists(workspace: Workspace | None) -> tuple[tuple[str, ...], dict[str, str]]:
    """Return enabled tools and explicit per-tool permissions for a workspace."""
    if workspace is None or not workspace.write:
        return READ_TOOLS, {"write_file": "never", "edit": "never", SHELL_TOOL: "never"}
    permissions = {
        "write_file": "always",
        "edit": "always",
        SHELL_TOOL: "always" if workspace.shell else "never",
    }
    return (*READ_TOOLS, *WRITE_TOOLS, *((SHELL_TOOL,) if workspace.shell else ())), permissions


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str) and value.isascii() and value.isprintable():
        return json.dumps(value)
    raise ValueError(f"vibe: cannot serialise {value!r} into the configuration")


def _dump_toml(document: Mapping[str, object]) -> str:
    scalars: list[str] = []
    sections: list[str] = []
    for key, value in document.items():
        if not re.fullmatch(r"[a-z_]+", key):
            raise ValueError(f"vibe: invalid configuration key {key!r}")
        if isinstance(value, list):
            for item in value:
                sections.append(
                    f"[[{key}]]\n" + "".join(f"{k} = {_toml_value(v)}\n" for k, v in item.items())
                )
        elif isinstance(value, dict):
            plain = {k: v for k, v in value.items() if not isinstance(v, dict)}
            nested = {k: v for k, v in value.items() if isinstance(v, dict)}
            if plain:
                sections.append(
                    f"[{key}]\n" + "".join(f"{k} = {_toml_value(v)}\n" for k, v in plain.items())
                )
            for name, table in nested.items():
                if not re.fullmatch(r"[a-z_]+", name):
                    raise ValueError(f"vibe: invalid configuration key {name!r}")
                sections.append(
                    f"[{key}.{name}]\n"
                    + "".join(f"{k} = {_toml_value(v)}\n" for k, v in table.items())
                )
        else:
            scalars.append(f"{key} = {_toml_value(value)}\n")
    text = "".join(scalars) + "".join(f"\n{section}" for section in sections)
    if tomllib.loads(text) != document:
        raise ValueError("vibe: the configuration does not round-trip through TOML")
    return text


#: Vibe 2.26.0's per-model ``thinking`` levels (``vibe/config_values.py``). Vibe maps
#: them to Mistral's ``reasoning_effort``: low -> "none", medium/high/max -> "high";
#: "off" sends none and the model reasons at its own default.
THINKING_LEVELS: Final = frozenset({"off", "low", "medium", "high", "max"})
#: ha's effort vocabulary (model_catalog.EFFORTS) onto Vibe's thinking levels.
_THINKING_BY_EFFORT: Final[Mapping[str, str]] = {
    "none": "low",
    "minimal": "low",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "max",
    "max": "max",
    "ultra": "max",
}


def thinking_for(effort: str) -> str:
    """Vibe's ``thinking`` level for ha's reasoning ``effort`` (amendment A3).

    Measured 2026-10-13 on Vibe 2.26.0 with Large 4: "low" ran the confinement
    probe 4/4 in 13-18 s; the default ("off") ran 0/3 complete, up to 279 s of
    reasoning before the first tool call.
    """
    try:
        return _THINKING_BY_EFFORT[effort]
    except KeyError:
        raise ValueError(f"vibe: unsupported reasoning effort {effort!r}") from None


def vibe_config(*, model: str, permissions: Mapping[str, str], thinking: str | None = None) -> str:
    """Build Vibe's isolated per-run configuration."""
    validate_model(model)
    if thinking is not None and thinking not in THINKING_LEVELS:
        raise ValueError(f"vibe: unsupported thinking level {thinking!r}")
    for tool, permission in permissions.items():
        if permission not in _PERMISSIONS or not re.fullmatch(r"[a-z_]+", tool):
            raise ValueError(f"vibe: unsupported permission {permission!r} for tool {tool!r}")
    document: dict[str, object] = {
        "active_model": model,
        "enable_telemetry": False,
        "enable_auto_update": False,
        "enable_update_checks": False,
        "enable_connectors": False,
        "enable_notifications": False,
        "include_project_context": False,
        "include_commit_signature": False,
        "vibe_base_url": CLOSED_LOOPBACK,
        "vibe_code_sessions_base_url": CLOSED_LOOPBACK,
        "console_base_url": CLOSED_LOOPBACK,
        "models": [
            {"name": model, "provider": "mistral", "alias": model}
            | ({} if thinking is None else {"thinking": thinking})
        ],
        "session_logging": {"enabled": False, "generate_titles": False},
        "experiments": {"enable": False},
    }
    if permissions:
        document["tools"] = {
            tool: {"permission": permissions[tool]} for tool in sorted(permissions)
        }
    return _dump_toml(document)


def build_vibe_command(
    *, workdir: Path, enabled: Sequence[str], max_turns: int, executable: str = "vibe"
) -> list[str]:
    """Build the programmatic command; callers provide the prompt on stdin."""
    if not enabled:
        raise ValueError("vibe: at least one tool must be enabled (none would enable all)")
    if max_turns < 1:
        raise ValueError("vibe: max_turns must be at least 1")
    command = [executable, "--workdir", str(workdir), "--agent", "accept-edits"]
    for tool in enabled:
        command.extend(("--enabled-tools", tool))
    command.extend(("--max-turns", str(max_turns), "--output", "streaming", "-p"))
    return command


#: Effect names that only read. Everything else -- including a tool a later Vibe
#: adds -- counts as a possible write until it is classified here.
READ_EFFECTS: Final[frozenset[str]] = frozenset({"file_system.read_file", "search_tool_functions"})
#: The effect names that write a file (confinement evidence).
WRITE_EFFECTS: Final = frozenset({"file_system.write_file", "file_system.search_replace"})


def _effects(entries: Sequence[Mapping[str, object]]) -> list[Mapping[str, object]]:
    return [entry for entry in entries if entry.get("type") == "effect"]


def _tool(effect: Mapping[str, object]) -> str | None:
    detail = effect.get("detail")
    tool = detail.get("toolName") if isinstance(detail, dict) else None
    return tool if isinstance(tool, str) and tool else None


def _state(effect: Mapping[str, object]) -> Mapping[str, object]:
    state = effect.get("state")
    return state if isinstance(state, dict) else {}


def denied(effect: Mapping[str, object]) -> bool:
    """Whether Vibe's gate refused an effect before it ran."""
    state = _state(effect)
    error = state.get("error")
    return (
        state.get("status") == "failed"
        and isinstance(error, dict)
        and error.get("code") == "tool_denied"
    )


def _text(entry: Mapping[str, object]) -> str:
    content = entry.get("content")
    if not isinstance(content, list):
        return ""
    return "".join(
        str(block.get("text") or "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ).strip()


def _is_assistant(entry: Mapping[str, object]) -> bool:
    return entry.get("type") == "message" and entry.get("role") == "assistant"


def last_assistant_text(entries: Sequence[Mapping[str, object]]) -> str | None:
    """Return the last assistant text, if present."""
    for entry in reversed(entries):
        if _is_assistant(entry):
            return _text(entry) or None
    return None


def _readable_prefix(events_log: Path) -> list[dict[str, object]]:
    try:
        text = events_log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    entries: list[dict[str, object]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except (ValueError, RecursionError):
            break
        if not isinstance(entry, dict):
            break
        entries.append(entry)
    return entries


def stream_error(events_log: Path) -> str | None:
    """Describe why a stream does not prove a completed run."""
    entries = read_events(events_log)
    if entries is None:
        return "vibe's output is not JSON lines (interrupted, or an unexpected format): the run is not valid"
    for entry in entries:
        if entry.get("type") == "notice" and entry.get("level") == "error":
            return f"vibe reported an error: {entry.get('message') or 'no message'}"
    if not entries or not _is_assistant(entries[-1]) or not _text(entries[-1]):
        return "vibe ended without a final answer (turn limit reached, or the run was cut short)"
    return None


def extract_report(events_log: Path, report_log: Path) -> None:
    """Write the last readable assistant text; it is not effect evidence."""
    report_log.write_text(last_assistant_text(_readable_prefix(events_log)) or "", encoding="utf-8")


def tool_call_started(events_log: Path) -> bool:
    """Return whether any call started, failing closed on an unreadable stream."""
    entries = read_events(events_log)
    return True if entries is None else bool(_effects(entries))


def write_tool_started(events_log: Path) -> bool:
    """Return whether a possible write started, failing closed on an unreadable stream."""
    entries = read_events(events_log)
    return (
        True
        if entries is None
        else any(
            _tool(effect) not in READ_EFFECTS and not denied(effect) for effect in _effects(entries)
        )
    )


def tool_call_completed(events_log: Path) -> bool:
    """Return whether the stream proves a non-read effect completed."""
    entries = read_events(events_log)
    return (
        False
        if entries is None
        else any(
            _state(effect).get("status") == "completed" and _tool(effect) not in READ_EFFECTS
            for effect in _effects(entries)
        )
    )


def count_tools(events_log: Path | None) -> dict[str, int] | None:
    """Count effects by tool name and unique entry id, or return no measurement."""
    entries = read_events(events_log)
    if entries is None:
        return None
    seen: set[str] = set()
    counts: Counter[str] = Counter()
    for effect in _effects(entries):
        entry_id, tool = effect.get("id"), _tool(effect)
        if not isinstance(entry_id, str) or tool is None:
            return None
        if entry_id not in seen:
            seen.add(entry_id)
            counts[tool] += 1
    return dict(counts)


def build_vibe_home(*, root: Path, name: str, config_text: str) -> Path:
    """Create one private HOME, Vibe configuration and empty private workdir."""
    home = root / name
    home.mkdir(parents=True, exist_ok=True)
    home.chmod(0o700)
    vibe_home = home / ".vibe"
    vibe_home.mkdir(mode=0o700, exist_ok=True)
    vibe_home.chmod(0o700)
    config = vibe_home / "config.toml"
    descriptor = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(config_text)
    private_workdir(home).mkdir(mode=0o700, exist_ok=True)
    return home


def private_workdir(home: Path) -> Path:
    """Return the empty workdir used when no workspace was supplied."""
    return home / "workdir"


def passthrough_refusal(profile: CapabilityProfile) -> str | None:
    """Refuse passthrough variables that override Vibe's configuration."""
    names = [name for name in profile.environment_passthrough if name.upper().startswith("VIBE_")]
    if not names:
        return None
    return f"vibe: environment passthrough {', '.join(sorted(names))} refused: a VIBE_* variable overrides ha's per-run configuration"


def resolve_api_key(ambient: Mapping[str, str]) -> tuple[str | None, str | None]:
    """Resolve Mistral's preset key without exposing its value in errors."""
    try:
        found = preset_key("mistral", ambient)
    except KeysError as exc:
        return None, f"vibe: no usable Mistral key: {exc}"
    if found is None:
        return (
            None,
            f"vibe: no Mistral key: set {_KEY_VARIABLE}, or declare a key file for mistral in keys.toml",
        )
    return found.value, None


def child_environment(
    home: Path, ambient: Mapping[str, str], profile: CapabilityProfile, *, api_key: str
) -> dict[str, str]:
    """Build the child's allowlisted environment from scratch."""
    child = {
        "HOME": str(home),
        "VIBE_HOME": str(home / ".vibe"),
        "TMPDIR": str(home),
        "PATH": ambient.get("PATH", "/usr/bin:/bin"),
        "LANG": ambient.get("LANG", "C.UTF-8"),
        "TERM": "dumb",
        AGENT_MARKER_VARIABLE: "vibe",
        _KEY_VARIABLE: api_key,
    }
    for name in profile.environment_passthrough:
        if name in ambient and name not in child and not name.upper().startswith("VIBE_"):
            child[name] = ambient[name]
    return child


def _effective_timeout(timeout_seconds: float, deadline: float | None) -> float:
    if deadline is None:
        return timeout_seconds
    return max(0.0, min(timeout_seconds, deadline - time.monotonic()))


def _refusal(
    model: str, profile: CapabilityProfile, ambient: Mapping[str, str]
) -> tuple[str | None, str | None]:
    """``(reason, None)`` to refuse before anything starts (exit 2), else ``(None, key)``."""
    try:
        validate_model(model)
    except ValueError as exc:
        return str(exc), None
    if profile.mcp is not None:
        return "vibe: MCP servers are not supported on this rail (spec D13): run refused", None
    reason = passthrough_refusal(profile)
    if reason is not None:
        return reason, None
    key, reason = resolve_api_key(ambient)
    if key is None:
        return reason or "vibe: no Mistral key", None
    return None, key


def _append(stderr_log: Path, message: str) -> None:
    with stderr_log.open("a", encoding="utf-8") as stream:
        stream.write(f"{message}\n")


def _failure_exit_code(
    events_log: Path, native: int, workspace: Workspace | None, *, killed: bool
) -> int:
    """A failure: replayable (3) only when the stream proves nothing could have written.

    Entries are emitted on completion, so a process killed by a signal may hide the
    write it was making: in a writable workspace that is never replayable.
    """
    writable = workspace is not None and workspace.write
    if tool_call_completed(events_log) or (writable and (killed or write_tool_started(events_log))):
        return failure_code_after_a_write(native)
    return PROVIDER_FALLBACK_EXIT_CODE


def run_vibe(
    *,
    prompt: str,
    name: str,
    model: str,
    timeout_seconds: float,
    max_turns: int,
    events_log: Path,
    report_log: Path,
    stderr_log: Path,
    profile: CapabilityProfile,
    environment: Mapping[str, str] | None = None,
    executable: str = "vibe",
    ephemeral_root: Path | None = None,
    deadline: float | None = None,
    temp_prefix: str = "headless-agents-",
    workspace: Workspace | None = None,
    reasoning_effort: str = "medium",
) -> int:
    """Run one ``vibe -p`` and return its classified code (spec §3.7)."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    ambient = dict(environment) if environment is not None else dict(os.environ)
    root = ephemeral_root if ephemeral_root is not None else default_ephemeral_root(ambient)
    for path in (events_log, report_log, stderr_log):
        path.parent.mkdir(parents=True, exist_ok=True)
    report_log.write_text("", encoding="utf-8")
    if workspace is not None:
        try:
            refuse_home_under_workspace(
                root if root is not None else Path(tempfile.gettempdir()), workspace
            )
        except ValueError as exc:
            stderr_log.write_text(f"vibe: {exc}\n", encoding="utf-8")
            return INVALID_USAGE_EXIT_CODE
    # Spec §3.7: a refusal before start is 2, nothing created, nothing spawned.
    reason, api_key = _refusal(model, profile, ambient)
    if reason is None:
        try:
            thinking = thinking_for(reasoning_effort)
        except ValueError as exc:
            reason = str(exc)
    if reason is not None or api_key is None:
        stderr_log.write_text(f"{reason}\n", encoding="utf-8")
        return INVALID_USAGE_EXIT_CODE
    enabled, permissions = tool_lists(workspace)
    config_text = vibe_config(model=model, permissions=permissions, thinking=thinking)

    def _run(base: Path) -> int:
        home = build_vibe_home(root=base, name=name, config_text=config_text)
        workdir = workspace.path if workspace is not None else private_workdir(home)
        command = build_vibe_command(
            workdir=workdir, enabled=enabled, max_turns=max_turns, executable=executable
        )
        remaining = _effective_timeout(timeout_seconds, deadline)
        if remaining <= 0:
            stderr_log.write_text(
                "vibe not launched: the caller's deadline had already expired\n", encoding="utf-8"
            )
            return TIMEOUT_EXIT_CODE
        with (
            events_log.open("w", encoding="utf-8") as events_stream,
            stderr_log.open("w", encoding="utf-8") as stderr_stream,
        ):
            try:
                process, lifeline = spawn_watched(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=events_stream,
                    stderr=stderr_stream,
                    cwd=home,
                    env=child_environment(home, ambient, profile, api_key=api_key),
                    text=True,
                    start_new_session=True,
                    preexec_fn=preexec_for(os.getpid()),
                )
            except OSError as exc:
                stderr_stream.write(f"unable to start vibe: {exc}\n")
                return PROVIDER_FALLBACK_EXIT_CODE
            timed_out = False
            try:
                try:
                    # communicate() writes the prompt, closes stdin (an open stdin hangs
                    # vibe, measured s10) and waits, all under one timeout.
                    process.communicate(input=prompt, timeout=remaining)
                except subprocess.TimeoutExpired:
                    terminate_process_group(process)
                    timed_out = True
            except BaseException:
                terminate_process_group(process)
                raise
            finally:
                lifeline.release()
        extract_report(events_log, report_log)
        if timed_out:
            # Operator ruling 2026-10-13 (spec §3.7): vibe never returns 4. Its stream is
            # emitted on completion, so it can never prove that no tool call was issued --
            # the case capability.TIMEOUT_REPLAYABLE_EXIT_CODE excludes, as for claude.
            _append(
                stderr_log,
                f"vibe reached its deadline ({int(remaining)} s); its completion-only stream "
                "cannot prove that no tool call started: 124, the chain stops here",
            )
            return TIMEOUT_EXIT_CODE
        returncode = int(process.returncode or 0)
        if returncode != 0:
            native = returncode if returncode > 0 else 1
            return _failure_exit_code(events_log, native, workspace, killed=returncode < 0)
        error = stream_error(events_log)
        if error is not None:
            _append(stderr_log, error)
            return _failure_exit_code(events_log, 1, workspace, killed=False)
        return 0

    if root is not None:
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=temp_prefix, dir=str(root)) as temporary:
            return _run(Path(temporary))
    with tempfile.TemporaryDirectory(prefix=temp_prefix) as temporary:
        return _run(Path(temporary))


_READ_NOTE = "Use read_file inside the workspace."
_TOOLS_NOTE: Final = {"read": _READ_NOTE, "write": f"{_READ_NOTE} Write with write_file and edit."}


def _preamble_for(spec: RunSpec, workspace: Workspace | None) -> str:
    mode = "write" if workspace is not None and workspace.write else "read"
    return rail_preamble(spec, tools_note=_TOOLS_NOTE[mode])


class VibeProvider:
    """:class:`~headless_agents.protocol.AgentProvider` adapter over Mistral Vibe.

    ``spec.max_turns`` becomes ``--max-turns``; ``spec.reasoning_effort`` becomes
    the model's ``thinking`` level in the per-run config (:func:`thinking_for`):
    Vibe 2.26.0 has no per-run effort option.
    """

    name = "vibe"

    def __init__(self, *, ephemeral_root: Path | None = None) -> None:
        self._ephemeral_root = ephemeral_root

    def _root(self, environ: Mapping[str, str]) -> Path:
        if self._ephemeral_root is not None:
            return self._ephemeral_root
        return default_ephemeral_root(environ) or Path(tempfile.gettempdir())

    def build_command(self, spec: RunSpec) -> list[str]:
        refuse_unsupported(self.name, spec.output_schema)
        workspace = workspace_of(spec)
        environ = spec.environment if spec.environment is not None else os.environ
        home = self._root(environ) / spec.name
        enabled, _ = tool_lists(workspace)
        return build_vibe_command(
            workdir=workspace.path if workspace is not None else private_workdir(home),
            enabled=enabled,
            max_turns=spec.max_turns,
            executable=spec.executable or "vibe",
        )

    def child_environment(self, spec: RunSpec, environ: Mapping[str, str]) -> dict[str, str] | None:
        # Rebuilt inside run_vibe around the ephemeral HOME and the resolved key.
        return None

    def prepare_home(self, spec: RunSpec) -> Path | None:
        refuse_unsupported(self.name, spec.output_schema)
        environ = spec.environment if spec.environment is not None else os.environ
        _, permissions = tool_lists(workspace_of(spec))
        return build_vibe_home(
            root=self._root(environ),
            name=spec.name,
            config_text=vibe_config(
                model=spec.model,
                permissions=permissions,
                thinking=thinking_for(spec.reasoning_effort),
            ),
        )

    def tool_call_completed(self, spec: RunSpec) -> bool:
        return False if spec.events_log is None else tool_call_completed(spec.events_log)

    def run(self, spec: RunSpec) -> RunResult:
        refuse_unsupported(self.name, spec.output_schema)
        spec = spec.with_run_dir_defaults()
        assert (
            spec.events_log is not None
            and spec.report_log is not None
            and spec.stderr_log is not None
        )
        workspace = workspace_of(spec)
        context = None if spec.context is None else tuple(spec.context.to_list())
        prompt = prepend(_preamble_for(spec, workspace), spec.prompt)
        start = time.monotonic()
        tripwire = armed_run(workspace)
        exit_code = run_vibe(
            prompt=prompt,
            name=spec.name,
            model=spec.model,
            timeout_seconds=spec.timeout_seconds,
            max_turns=spec.max_turns,
            events_log=spec.events_log,
            report_log=spec.report_log,
            stderr_log=spec.stderr_log,
            profile=spec.profile,
            environment=spec.environment,
            executable=spec.executable or "vibe",
            ephemeral_root=self._ephemeral_root,
            deadline=spec.deadline,
            workspace=workspace,
            reasoning_effort=spec.reasoning_effort,
        )
        duration = time.monotonic() - start
        exit_code, git_tampered = settle_run(tripwire, exit_code, spec.stderr_log)
        return record(
            spec,
            RunResult(
                exit_code=exit_code,
                provider=self.name,
                model=spec.model,
                report_path=spec.report_log,
                events_log=spec.events_log,
                tokens=None,
                duration_seconds=duration,
                tool_call_completed=self.tool_call_completed(spec),
                cost_usd=None,
                text=answer_text(spec.report_log, exit_code=exit_code),
                run_id=run_id_of(spec),
                stderr_log=spec.stderr_log,
                workspace=workspace_summary(workspace, git_tampered),
                context=context,
            ),
        )
