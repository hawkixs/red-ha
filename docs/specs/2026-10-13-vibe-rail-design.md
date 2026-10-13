# headless-agents — the `vibe` CLI rail (Mistral Vibe) — design

- **Date**: 2026-10-13
- **Status**: design approved section by section by the operator (2026-10-09 to 2026-10-13);
  written spec awaiting review
- **Scope**: brain ticket 460b5ec4 ("Integrate Mistral Vibe as a headless CLI provider with
  workspace read/write support")
- **Operator decisions**: method `spec`; primary use is a write (build) worker, reads come
  with it; measure before the spec (a live campaign on the installed CLI, §2.1); approach A
  (`vibe -p --output streaming`) now, the ACP protocol evaluated later in its own ticket

## 1. Problem

ha's `mistral` provider is a text-only HTTP completion adapter: no tools, no workspace, no
MCP. Mistral models therefore cannot read or write a repository through ha, so they cannot
serve as a build worker or as a reviewer that inspects code. Mistral ships an agentic CLI,
Mistral Vibe, whose programmatic mode (`vibe -p`) runs file tools against a working
directory. This spec adds it as a new CLI rail named `vibe`. It sits beside the HTTP `mistral`
provider and does not change that provider.

A CLI rail is only acceptable in ha if it honours the same contracts as the four existing
ones:

- a per-run home that never reads the operator's configuration;
- a capability profile (read-only, write, write with shell) enforced by the rail rather
  than requested of the model;
- isolation and confinement proofs tied to the installed CLI version;
- classified exit codes (2 refused before start, 3 and 4 replayable, 1 after a possible
  write, 124 timeout);
- readable `report.log`, `events.jsonl` and `stderr.log`.

Vibe's own defaults break several of these. It fetches remote "connectors" from the Mistral
account and exposes them as tools. It sends telemetry and runs remote experiments. It keeps
session journals, and it lets `VIBE_*` environment variables override any configuration key.
In practice it exits 0 on failures. Section 2.1 lists what was measured.

## 2. Decisions

| # | Decision |
|---|---|
| D1 | A new CLI rail named `vibe`; the HTTP `mistral` provider is unchanged. |
| D2 | Drive `vibe -p --output streaming` with the prompt on stdin (approach A). The ACP protocol (`vibe acp`) is a follow-up ticket, to be evaluated when G5 (82f066ba) needs Vibe's usage data. |
| D3 | Supported version: Vibe 2.26.0, the version measured. Another version runs only once `ha prove vibe` has recorded proofs for it, as on every rail. |
| D4 | A model is mandatory (`vibe` is not in `MODEL_OPTIONAL`). ha writes the requested id as a `[[models]]` entry and pins `active_model`; Vibe's remote default routing is never reached. |
| D5 | The API key is the one `keys.toml` declares for the `mistral` preset, handed to the worker in `MISTRAL_API_KEY` only. No `.env` file is written, no keyring is consulted. |
| D6 | ha does not update Vibe. `ha providers --update vibe` reports "not updated" with the operator's commands (`uv tool upgrade mistral-vibe`, then `ha prove vibe`). The per-run configuration turns Vibe's auto-update off. |
| D7 | Every run gets an ephemeral `VIBE_HOME` with a configuration written by ha that turns every side channel off (§3.2). |
| D8 | The tool list is always explicit (`--enabled-tools`), backed by per-tool permissions in the configuration. `--trust`, `--yolo`, `--auto-approve`, `--add-dir` and `--worktree` are never passed. |
| D9 | Validity and failure classification come from the event stream, never from Vibe's exit code alone. |
| D10 | The final assistant text is the report; it is never evidence of an effect. Tool evidence comes from `effect` entries only. |
| D11 | `tokens`, `cost_usd` and `model_reported` are `None`: Vibe's stream carries none of them. |
| D12 | Vibe's shell is not sandboxed: `vibe` joins `_UNSANDBOXED_SHELL_RAILS`, so a `--shell` write is serialised. |
| D13 | MCP is supported only if the first plan task measures Vibe's MCP tool naming and a bearer passed by environment; otherwise a run with an MCP server is refused (exit 2) and the README declares MCP unsupported. |
| D14 | The vendor rule keeps `vibe` and `mistral` as distinct provider names; a shared-vendor rule is a carry-forward. |

### 2.1 Measured facts the design relies on

Measured on 2026-10-09 against Vibe 2.26.0 with `mistral-large-latest`. Each of 20 scenarios
had its own fresh `VIBE_HOME`, fresh fake `HOME`, disposable workspace and outside
directory. The key was set in `MISTRAL_API_KEY` only and the prompt went on stdin. The
command was `--output streaming -p` with turn, token and price caps. Appendix A holds the
table.

- **Runtime.** The default runtime is the "Unified Harness", a native extension; the Python
  package is the host side. Facts about the native side were measured, not read.
- **Tool gate.** A tool absent from `--enabled-tools` is still visible to the model, but
  every call to it fails with `state.error.code == "tool_denied"` ("Tool execution denied by
  approval policy"), and nothing runs (s16). Adding `[tools.<name>] permission = "never"`
  gives the same result (s17).
- **Approvals in `-p`.** A call that needs approval is denied at once with
  `tool_denied` ("Tool execution denied by approval callback"), preceded by a `callback`
  entry. This covers a path outside the workspace, a symlink in the workspace that resolves
  outside it, and `bash` under the `accept-edits` agent (s3 to s6). Nothing hangs.
- **Repository configuration.** Without `--trust`, the repository's `.vibe/config.toml` and
  `AGENTS.md` are ignored. A planted `bypass_tool_permissions = true` had no effect (s7).
- **Code-mode meta-tools.** `run_typescript` and `search_tool_functions` are always
  offered, and `--disabled-tools` does not remove them (s19). `run_typescript` has no
  `fetch`, no `fs`, no `Deno` and no `require`: there was no network connection and no
  outside write (s20). Its file effects go through the same gate (s15, s16).
  `run_typescript` calls themselves do not appear in the stream; their inner effects do.
- **Exit codes.** An exhausted `--max-turns` exits 0 with an empty stderr and no final
  assistant message (s12). SIGINT exits 0 and writes "Bye!" into stdout, breaking the JSONL
  (s9). SIGTERM ends the process with -15 and leaves no process behind (s8).
- **stdin.** `-p TEXT` with a stdin pipe that is never closed hangs before any model call
  (s10). The prompt must be written to stdin and stdin closed.
- **Defaults.** With defaults, a run writes a connector cache, an experiment cache, a
  `whoami` cache and a full session journal under `VIBE_HOME` (s1).
- **Hardened configuration.** With the configuration of §3.2, a run leaves only
  `config.toml`, an empty `trusted_folders.toml`, `vibehistory`, an empty `logs/vibe.log`
  and empty session directories. The only network peer is `api.mistral.ai` once the three
  base URLs point at a closed loopback port (s2, s13). Nothing is written to `HOME` outside
  `VIBE_HOME`.
- **Reports.** The final assistant text claimed writes that the gate had denied (s16).
- **No usage data.** No usage, cost or provider-reported model appears in the stream.
- **Configuration layers** (read from source). The `VIBE_` environment layer overrides
  every top-level configuration key, with `__` as the nested delimiter. A project layer is
  read from `.vibe/config.toml`. `VIBE_HOME` defaults to `~/.vibe`. `MISTRAL_API_KEY` is
  read before a `.env` file and before the keyring.

## 3. Design

### 3.1 Registration

`vibe` is added everywhere a rail is enumerated, so that no table falls back to an unsafe
default. Leaving `vibe` out of `CLI_RAILS` would let it run with no isolation proof.

| Table | Entry |
|---|---|
| `registry._FACTORIES` | `"vibe": VibeProvider` (display order: after `opencode`) |
| `registry._MAX_PROMPT_BYTES` | `None` (stdin) |
| `registry._TOOL_COUNTERS` | `vibe.count_tools` |
| `registry._RAIL_HOME_PASSTHROUGH` | `frozenset({"VIBE_HOME"})` (version probe only) |
| `proofs.CLI_RAILS` | appended |
| `proofs._ISOLATION_SOURCE_FILES` | `("providers/vibe.py",)` |
| `prove.AUTH` / `prove.EXPOSED` | no credential files (the key travels by environment) |
| `engine.DEFAULT_CREDENTIALS` | `()` |
| `engine._UNSANDBOXED_SHELL_RAILS` | add `vibe` |
| `updaters.UPDATERS` | an entry that never runs a command (D6) |
| `model_catalog` / `catalog.toml` | a `vibe` table; `vibe` is not in `EFFORT_OPTIONAL_PROVIDERS` |
| `model_live.QUERIED_PROVIDERS` | not added (Vibe has no model-list command) |

Prose that counts rails ("four CLI rails", "three rails out of four", "eight rails") is
updated in code, tests and the README.

### 3.2 Per-run home and configuration

The provider builds a fresh ephemeral home with the same root selection as opencode,
refused under the workspace (`refuse_home_under_workspace`). `HOME/.vibe` becomes
`VIBE_HOME`, and ha writes its `config.toml`:

```toml
active_model = "<model>"
enable_telemetry = false
enable_auto_update = false
enable_update_checks = false
enable_connectors = false
enable_notifications = false
include_project_context = false
include_commit_signature = false
vibe_base_url = "http://127.0.0.1:9"
vibe_code_sessions_base_url = "http://127.0.0.1:9"
console_base_url = "http://127.0.0.1:9"

[[models]]
name = "<model>"
provider = "mistral"
alias = "<model>"

[session_logging]
enabled = false
generate_titles = false

[experiments]
enable = false

# per profile, §3.4
[tools.write_file]
permission = "never" | "always"
[tools.edit]
permission = "never" | "always"
[tools.bash]
permission = "never" | "always"
```

The model id is validated before use (no quote, no newline, no TOML metacharacter), and the
file is written with a TOML serialiser, never by string formatting.

### 3.3 Child environment and command

The environment is an allowlist built from scratch:

- `HOME` (the ephemeral home), `VIBE_HOME`, `PATH` and `LANG`;
- `TERM=dumb`;
- `TMPDIR` inside the home;
- `MISTRAL_API_KEY`;
- `AI_AGENT=vibe`;
- the profile's `environment_passthrough`, which never overrides a key ha has set.

A passthrough naming a `VIBE_*` variable is refused with exit 2 before anything starts.
That layer would override ha's configuration. A missing key is also refused (exit 2) and
the message names the `keys.toml` entry, never a value.

```text
vibe --workdir <workspace | empty private directory> --agent accept-edits
     --enabled-tools <tool> ... [--max-turns <RunSpec.max_turns>]
     --output streaming -p
```

- **Process.** The process runs with `cwd` set to the ephemeral home and
  `start_new_session=True`, under `procgroup.spawn_watched`.
- **Prompt.** The prompt (rail preamble plus task) is written to stdin, and stdin is then
  closed.
- **Output.** stdout goes to `events.jsonl` and stderr to `stderr.log`.
- **Price cap.** `--max-price` is not passed: Vibe prices from the configured
  `input_price`/`output_price`, which are 0 for a model ha declares, so the cap would be
  illusory. A real cost cap is ticket fe73f648.

### 3.4 Capability profiles

`--enabled-tools` with no value enables every tool (s11), so ha always passes at least one.

| Profile | `--enabled-tools` | `permission = "never"` | `permission = "always"` |
|---|---|---|---|
| no workspace | `read_file` (workdir: an empty private directory) | write_file, edit, bash | — |
| read-only workspace | `read_file`, plus `grep` if §4 measures it present | write_file, edit, bash | — |
| write | the read-only list plus `write_file`, `edit` | bash | write_file, edit |
| write + shell | the write list plus `bash` | — | write_file, edit, bash |

The `accept-edits` agent already grants `write_file` and `edit`; the explicit permissions
make the profile independent of the agent's defaults. The workspace boundary and `.git`
protection come from Vibe's gate (an outside path needs approval, which `-p` denies; a
write to `.git/hooks` or `.git/config` is escalated). ha's git tripwire (`armed_run`,
`settle_run`) still guards `.git` as on every rail.

The code-mode meta-tools stay on: they cannot be disabled, and their effects pass the same
gate. The README says so as an accepted, measured limit.

### 3.5 MCP

If the first MCP plan task succeeds, `profile.mcp` becomes an `[[mcp_servers]]` entry
(`transport = "streamable-http"`, `url`, `auth.api_key_env = <bearer variable>`). The
bearer stays in the child environment and never touches disk, as with opencode. The
server's tools are added to `--enabled-tools` by glob. That task measures the tool name
Vibe exposes, whether the static auth header is sent, and whether a completed MCP call
shows up as an `effect`. If any of those fails, D13 applies: refusal with exit 2 and
"unsupported" in the README.

### 3.6 Event stream, report and validity

- **Stream format.** `events.jsonl` holds one JSON object per line, each emitted on
  completion.
- **Report.** `report.log` receives the text of the last `message` entry with
  `role == "assistant"`.
- **Valid run.** A run is valid only if all of these hold:
  - the exit code is 0;
  - every stdout line is a JSON object;
  - the last entry is a non-empty assistant message;
  - no `notice` entry has `level == "error"`.

  Anything else is a failure, whatever the exit code.

Predicates, all fail-closed (an unreadable or absent stream counts as "may have started"):

- `tool_call_started`: at least one `effect` entry exists.
- `tool_call_completed`: some `effect` with `state.status == "completed"` on a tool outside
  the read set (`file_system.read_file`, `search_tool_functions`, and the grep tool's effect name if §4 finds it offered).
- `write_tool_started`: some `effect` on a tool outside the read set, unless it is `failed`
  with `error.code == "tool_denied"`, which proves it never ran. The set is inverted on
  purpose: a tool Vibe adds later counts as a write until it is classified.

`count_tools` counts `effect` entries by `detail.toolName`, deduplicated by `id`. It
returns `None` if the stream is unreadable. The counts cover gated calls; `run_typescript`
invocations are not visible.

### 3.7 Exit codes

| Situation | Code |
|---|---|
| refused before start (passthrough `VIBE_*`, missing key, unsupported MCP, home under workspace, invalid model id) | 2 |
| launch failure (`OSError`), or invalid run and nothing could have written | 3 |
| any deadline: a caller deadline already expired before launch (nothing launched), or the runner's own deadline during the run | 124 (never 4, see below) |
| invalid run after a possible write | `failure_code_after_a_write` → 1 |
| valid run | 0 |

Vibe never returns 4 (operator ruling, 2026-10-13, after round 3 of the plan review). Its
stream is emitted on completion, so it can never prove that no tool call was issued, which is
what `TIMEOUT_REPLAYABLE_EXIT_CODE` requires. claude is in the same position. A timed-out
vibe link therefore stops a chain.

A timeout sends SIGTERM to the process group, then SIGKILL after the grace period
(`terminate_process_group`). ha never sends SIGINT, which Vibe turns into exit 0.

Vibe's API error codes (`rate_limit`, `invalid_api_key`, `invalid_model`, `backend_error`)
were not provoked during the measurement campaign, to save quota. A plan task provokes them
with an invalid key and an invalid model id and records the fixtures. Until then, an API
failure with no effect started maps to 3.

### 3.8 Proofs

**Isolation** (`ha prove vibe`, two modes: with and without a workspace). The operator home
of the proof is planted with:

- `~/.vibe/config.toml` declaring a stdio MCP server and a user hook, each touching a
  sentinel (the hook format is read from Vibe's `core/hooks` during planning);
- `~/.vibe/.env` with a marker variable;
- a user-level instruction file with a marker, at the location Vibe actually reads (found
  in source during planning).

The workspace is planted with the standard `CLAUDE.md`, `AGENTS.md` and `GEMINI.md`, plus a
`.vibe/config.toml` setting `bypass_tool_permissions = true`.

The proof fails on any user marker in the answer, on a repository marker in the answer
without a tool read, or on any sentinel.

**Confinement** (`ha prove vibe --confinement`) uses the three standard outside targets. A
new `refused_attempts` branch accepts an `effect` entry that meets all of these:

- `state.status == "failed"`;
- `state.error.code == "tool_denied"`;
- the tool is a write tool;
- `detail.input.filePath` resolves to the target.

This is the shape measured in s4. The byte check of the targets stays the primary verdict.
A passed proof puts the rail in `mode parallel`. Without one, writes are `serialised`.

### 3.9 Documentation

A README section covers:

- the supported version, installation (`uv tool install mistral-vibe==2.26.0`) and the key;
- model selection and the profiles;
- the measured limits: no usage or cost, an unreliable final report, the code-mode
  meta-tools, an unconfined shell, and a SIGINT that exits 0;
- the MCP status and the unverified capabilities.

The CHANGELOG gets an entry under Unreleased.

## 4. Testing

- **Strict TDD.** Each behaviour starts with a failing unit test in
  `tests/unit/headless_agents/test_provider_vibe.py`. It covers:
  - command and configuration per profile;
  - the environment allowlist and the `VIBE_*` refusal;
  - model id validation;
  - stream validity;
  - each predicate, fail-closed;
  - the exit-code table of §3.7;
  - `count_tools` and report extraction.
- **Fixtures.** The fixtures are real streams recorded in the measurement campaign (s2, s4,
  s9, s12, s16), with private paths rewritten, under `tests/unit/headless_agents/fixtures/vibe/`.
- **Run path.** The `run` path is exercised with a fake `vibe` executable, and with a fake
  `Popen` for timeouts, in the pattern of the opencode tests.
- **Rail enumerations.** Tests that enumerate rails (registry, proofs, updaters,
  `cli_providers_update`, catalog) are updated, and new tests assert the `vibe` entries of
  §3.1.
- **Proofs.** The `refused_attempts` branch is tested on the recorded s4 stream, and
  isolation planting on a temporary home.
- **Measurement tasks** (plan, before the code they inform; they spend a little quota, run
  with the operator's go):
  - MCP (§3.5);
  - API errors (§3.7);
  - whether `grep` is offered;
  - Vibe's user instruction and hook locations.
- **Live tests** (`HA_LIVE=1`, never in CI, with the operator's go): one `ha run vibe`
  read and one write, then `ha prove vibe` and `ha prove vibe --confinement` on 2.26.0.
- **Gates.** `make ci` and `rail check` are green before the PR.

## 5. Non-goals

- Driving Vibe through ACP, ha-side approval callbacks, and Vibe usage and cost data:
  follow-up ticket, linked to G5.
- A cost cap: fe73f648.
- Updating or rolling back Vibe from ha (D6).
- A shared vendor identity between `vibe` and `mistral` (D14).
- A `build-vibe` preset in `ha init`: added once the rail holds a confinement proof.
- Vibe versions other than 2.26.0 until proven.
- Vibe's `--worktree`, `--resume` / `--continue` lineages, subagents, skills, voice and the
  interactive TUI.

## 6. Success criteria

- `ha providers` lists `vibe` with its version and proof state.
- `ha run vibe -m <mistral model>` performs a read-only review and a workspace write. Each
  produces a classified exit code and readable `report.log`, `events.jsonl` and
  `stderr.log`. `ha show` displays them.
- `ha prove vibe` passes isolation on 2.26.0. `ha prove vibe --confinement` either passes or
  reports why it is inconclusive, and the mode follows.
- No configuration, credential, session or cache of the operator's `~/.vibe` is read or
  written by a run, as shown by the isolation proof and a before/after listing.
- The HTTP `mistral` provider behaves exactly as before.
- `make ci` and `rail check` are green; the PR carries `## Carry-forwards` and says what was
  not verified.

## Appendix A — measurement campaign (Vibe 2.26.0, 2026-10-09)

| # | Scenario | Result |
|---|---|---|
| s1 | defaults except model | exit 0, 9.0 s; connector cache (+lock), experiment cache, whoami cache, full session journal under `logs/session/unified/<id>/` |
| s2 | hardened configuration | exit 0, 4.1 s; only `config.toml`, empty `trusted_folders.toml`, `vibehistory`, empty `logs/vibe.log`, empty session directories; peers: `api.mistral.ai` and one chat/console host |
| s3 | read an absolute path outside | `callback` entry, then `effect` failed `tool_denied` "approval callback"; nothing read |
| s4 | write inside, then outside | inside write `completed` (approval source `config`); outside write `callback` + failed `tool_denied`; outside untouched |
| s5 | read a workspace symlink to an outside file | `callback` + failed `tool_denied`; nothing read |
| s6 | `bash` enabled, `accept-edits` | `callback` + failed `tool_denied`; nothing ran |
| s7 | repository `.vibe/config.toml` with `bypass_tool_permissions` and `AGENTS.md`, no `--trust` | stderr: project configuration ignored; bypass not applied; marker absent |
| s8 | SIGTERM after 6 s | exit -15, no process left |
| s9 | SIGINT after 6 s | exit 0, "Bye!" written into stdout |
| s10 | `-p TEXT`, stdin pipe never closed | hangs before any model call |
| s11 | no `--enabled-tools` | model lists every tool, including `bash`, `cron`, `run_typescript` |
| s12 | `--max-turns 2` on a 6-turn task | exit 0, empty stderr, no final assistant message |
| s13 | s2 plus the three base URLs on `http://127.0.0.1:9` | exit 0; only `api.mistral.ai` remains |
| s14 | `--enabled-tools read_file write_file edit` | model still lists `bash`, `skill`, `run_typescript`, `search_tool_functions` |
| s15 | all tools, `run_typescript` writes a file | inner `search_tool_functions` and `file_system.write_file` effects visible, `run_typescript` itself not; file written |
| s16 | only `read_file` enabled; ask write, bash, `run_typescript` write | every attempt failed `tool_denied` "approval policy"; nothing written; final report claims success |
| s17 | s16 plus `permission = "never"` | same as s16 |
| s18 | s16 with `--agent plan` | same as s16 |
| s19 | `--disabled-tools run_typescript search_tool_functions` | not removed: `search_tool_functions` still ran |
| s20 | `run_typescript`: `fetch`, `Deno`, `require`, `node:fs` | `fetch` "not supported in the sandbox"; `Deno` and `require` undefined; no connection to the target host; outside untouched |

## Amendments

### A1 — Task 0 measurements (2026-10-13, Vibe 2.26.0, `mistral-large-latest`)

- **API errors (M1).** An invalid `MISTRAL_API_KEY` and an unknown model id both exit 1.
  stdout holds only the user `message` entry: no `notice`, no effect. The reason is on
  stderr only (`Error: Invalid API key ...`; `Error: API error occurred: Status 400 ...
  "type":"invalid_model"`). The rail classifies both as 3 (invalid run, nothing could have
  written).
- **grep (M2).** The default runtime has no grep tool. Asked to grep, the model fell back to
  `bash`, which was denied. The read-only profile enables `read_file` only (§3.4).
- **MCP (M3).** A `streamable-http` server with static auth from an environment variable
  connects, and its tools are listed and named `mcp_<server>.<tool>` in effects. A call
  needs approval by default and is therefore denied in `-p`. A per-tool permission or an
  `--enabled-tools` glob that admits the call was not established in two attempts, and
  connecting also writes an MCP descriptor cache under `logs/mcp-descriptors/`. D13
  therefore applies: **MCP is unsupported in v1**, a run with an MCP server is refused with
  2, and the README says so. A follow-up ticket carries these measurements.
- **User configuration (M4, from source).**
  - User-level instructions: `$VIBE_HOME/AGENTS.md` (`vibe/core/system_prompt.py`).
  - User hooks: `$VIBE_HOME/hooks.toml`, made of `[[hooks]]` entries with `name`, `type`
    (`post_agent`, `pre_tool`, `post_tool`) and `command` (`vibe/core/hooks/config.py`).

  The isolation proof plants both.

