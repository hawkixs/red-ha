"""engine.plan(): every gate that needs neither git nor mutable state (spec 0.5.0 §3.4, §3.8.2).

A unit test drives the engine API directly with each combination the CLI
refuses, and expects the same refusal: a gate only one entry point enforces
is the defect red-lab paid for (f9eff72c).
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from headless_agents.budget import Caps
from headless_agents.context import ContextBundle
from headless_agents.engine import (
    REVIEW_DEFAULT_TASK,
    Overrides,
    Request,
    UsageError,
    _spec_for,
    panel_members,
    plan,
)
from headless_agents.registry import max_prompt_bytes
from headless_agents.runs import Registry
from headless_agents.templates import implement_prompt


@dataclass
class Env:
    home: Path
    cwd: Path
    environ: dict[str, str] = field(default_factory=dict)

    @property
    def config(self) -> Path:
        return self.home / ".config" / "ha"

    def roles(self, text: str) -> None:
        self.config.mkdir(parents=True, exist_ok=True)
        (self.config / "roles.toml").write_text(text)

    def mcp(self, text: str) -> None:
        self.config.mkdir(parents=True, exist_ok=True)
        (self.config / "mcp.toml").write_text(text)

    @property
    def state(self) -> Path:
        return (self.home / ".local" / "state" / "ha").resolve()

    def register(self, run_id: str, *, target: dict[str, str], lineage: str | None) -> None:
        Registry(self.state, runs_root=self.home / ".cache" / "ha" / "runs").create(
            run_id, run_dir=None, target=target, repository=self.cwd, lineage=lineage
        )

    def request(
        self,
        target: str,
        prompt: str | None,
        *,
        overrides: Overrides | None = None,
        stdin_is_tty: bool = False,
        base: str | None = None,
        repo: Path | None = None,
        run_dir: Path | None = None,
        continue_run: str | None = None,
        head: str | None = None,
        review_run: str | None = None,
        output_schema: dict[str, object] | None = None,
    ) -> Request:
        return Request(
            target=target,
            prompt=prompt,
            stdin_is_tty=stdin_is_tty,
            overrides=overrides or Overrides(),
            base=base,
            repo=repo,
            run_dir=run_dir,
            continue_run=continue_run,
            head=head,
            review_run=review_run,
            output_schema=output_schema,
            cwd=self.cwd,
            environ={"PATH": "/usr/bin:/bin", "HOME": str(self.home), **self.environ},
            home=self.home,
        )


@pytest.fixture
def env(tmp_path: Path) -> Env:
    home = tmp_path / "home"
    (home / ".config" / "ha").mkdir(parents=True)
    (home / ".config" / "ha" / "models.toml").write_text(
        'codex = "codex-default"\nclaude = "claude-default"\nopencode = "oc-default"\n'
        'openrouter = "or-default"\n"openai-compat" = "oc-model"\n'
    )
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    return Env(home=home, cwd=cwd)


@pytest.fixture
def no_subprocess(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[object]]:
    """plan() runs no git -- and no subprocess at all (spec §3.8.2)."""
    calls: list[object] = []

    def refuse(*args: object, **kwargs: object) -> None:
        calls.append(args)
        raise AssertionError(f"plan() started a subprocess: {args!r}")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    yield calls
    assert calls == []


@pytest.mark.parametrize(
    ("target", "overrides", "rule"),
    [
        ("codex", Overrides(shell=True), "shell requires write"),
        ("openrouter", Overrides(write=True), "write needs a CLI rail"),
        ("openrouter", Overrides(mcp="brain-read"), "mcp needs a CLI rail"),
        ("openai-compat", Overrides(), "openai-compat needs base_url and key_env"),
        ("codex", Overrides(base_url="http://x"), "apply to openai-compat only"),
        ("codex", Overrides(mcp="missing"), "mcp profile 'missing' is not in mcp.toml"),
        ("nobody", Overrides(), "unknown target 'nobody'"),
    ],
)
def test_the_engine_refuses_what_the_cli_refuses(
    env: Env, target: str, overrides: Overrides, rule: str
) -> None:
    env.mcp('[brain-read]\nurl = "http://127.0.0.1:8765/mcp"\nbearer_env = "T"\n')
    with pytest.raises(UsageError, match=rule):
        plan(env.request(target, "task", overrides=overrides))


def test_max_tokens_reaches_http_links_only(env: Env) -> None:
    env.roles('[r]\nchain = ["openrouter:m", "codex:m"]\nmax_tokens = 100\n')
    planned = plan(env.request("r", "task"))
    bundle = ContextBundle(level="none", files=())
    for provider in ("openrouter", "codex"):
        spec = _spec_for(
            provider, planned, bundle, run_id="test", run_dir=env.cwd / provider, workspace=None
        )
        if provider == "openrouter":
            assert spec.extra["max_tokens"] == 100
        else:
            assert "max_tokens" not in spec.extra


def test_the_flag_overrides_the_role(env: Env) -> None:
    env.roles('[r]\nprovider = "openrouter"\nmax_tokens = 100\n')
    planned = plan(env.request("r", "task", overrides=Overrides(max_tokens=64)))
    spec = _spec_for(
        "openrouter",
        planned,
        ContextBundle(level="none", files=()),
        run_id="test",
        run_dir=env.cwd / "openrouter",
        workspace=None,
    )
    assert spec.extra["max_tokens"] == 64


def test_base_needs_a_write_run(env: Env) -> None:
    with pytest.raises(UsageError, match="--base needs a write run"):
        plan(env.request("codex", "task", base="main"))


def test_a_write_role_is_planned_for_the_write_protocol(env: Env) -> None:
    """Plan Task 22 lifts the P5 refusal: a write run now reaches the write flow."""
    env.roles('[impl]\nprovider = "codex"\nwrite = true\n')
    assert plan(env.request("impl", "task")).role.write
    assert plan(env.request("codex", "task", overrides=Overrides(write=True))).role.write


def test_no_prompt_on_a_terminal_is_refused(env: Env) -> None:
    with pytest.raises(UsageError, match="no prompt"):
        plan(env.request("codex", None, stdin_is_tty=True))


@pytest.mark.parametrize("prompt", ["", "  \n\t"])
def test_an_empty_prompt_from_a_closed_pipe_is_refused(env: Env, prompt: str) -> None:
    """Review Focus 2: the CLI hands the engine what a closed or empty stdin gave."""
    with pytest.raises(UsageError, match="no prompt"):
        plan(env.request("codex", prompt))


def test_every_link_is_checked_for_prompt_size(env: Env) -> None:
    env.roles('[r]\nchain = ["codex:m", "agy"]\n')
    with pytest.raises(UsageError, match=r"agy.*120000.*bytes"):
        plan(env.request("r", "x" * 130_000))


def test_role_instructions_count_against_the_claude_bound(env: Env) -> None:
    env.roles(f'[r]\nprovider = "claude"\ninstructions = """{"i" * 131_100}"""\n')
    with pytest.raises(UsageError, match="131071"):
        plan(env.request("r", "short"))


def test_a_model_is_required_before_anything_runs(env: Env) -> None:
    (env.config / "models.toml").write_text("")
    with pytest.raises(UsageError, match="codex needs a model"):
        plan(env.request("codex", "task"))


def test_the_overrides_reach_the_plan(env: Env, no_subprocess: list[object]) -> None:
    env.roles('[rev]\nprovider = "codex"\neffort = "high"\ninstructions = "be terse"\n')
    planned = plan(
        env.request("rev", "task", overrides=Overrides(model="m1", timeout=42.0, context="none"))
    )
    assert planned.role.name == "rev"
    assert (planned.role.effort, planned.role.timeout, planned.role.context) == (
        "high",
        42.0,
        "none",
    )
    assert planned.models == {"codex": "m1"}
    assert planned.prompt == "task"
    assert planned.run_dir is None
    assert planned.state == (env.home / ".local" / "state" / "ha").resolve()


def test_the_role_model_is_used_without_m(env: Env) -> None:
    env.roles('[rev]\nprovider = "codex"\nmodel = "from-role"\n')
    assert plan(env.request("rev", "task")).models == {"codex": "from-role"}


def test_an_mcp_role_resolves_its_profile(env: Env) -> None:
    env.mcp('[brain-read]\nurl = "http://127.0.0.1:8765/mcp"\nbearer_env = "T"\n')
    env.roles('[r]\nprovider = "codex"\nmcp = "brain-read"\n')
    planned = plan(env.request("r", "task"))
    assert planned.mcp is not None and planned.mcp.bearer_env_var == "T"


def test_a_relative_run_dir_is_anchored_at_the_cwd(env: Env) -> None:
    planned = plan(env.request("codex", "task", run_dir=Path("out/run")))
    assert planned.run_dir == env.cwd / "out" / "run"


def test_a_run_dir_with_no_name_is_refused(env: Env) -> None:
    with pytest.raises(UsageError, match="has no name"):
        plan(env.request("codex", "task", run_dir=Path("/")))


def test_the_parent_session_does_not_leak_into_the_child(env: Env) -> None:
    env.environ.update({"CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "cli", "KEEP_ME": "yes"})
    planned = plan(env.request("codex", "task"))
    assert "CLAUDECODE" not in planned.environment
    assert "CLAUDE_CODE_ENTRYPOINT" not in planned.environment
    assert planned.environment["KEEP_ME"] == "yes"


def test_an_invalid_roles_file_is_a_usage_error(env: Env) -> None:
    env.roles('[a]\nprovider = "codex"\nshell = true\n')
    with pytest.raises(UsageError, match=r"roles\.toml: \[a\] shell requires write"):
        plan(env.request("codex", "task"))


def test_a_roles_file_linking_outside_the_config_dir_is_a_usage_error(
    env: Env, tmp_path: Path
) -> None:
    planted = tmp_path / "repo-roles.toml"
    planted.write_text('[x]\nprovider = "codex"\n')
    (env.config / "roles.toml").symlink_to(planted)
    with pytest.raises(UsageError, match="outside the configuration directory"):
        plan(env.request("codex", "task"))


def test_an_invalid_workflows_file_refuses_every_target(env: Env) -> None:
    """§3.2: validated before anything runs, whatever the target -- a provider's included."""
    (env.config / "workflows.toml").write_text(
        '[build]\nshape = "implement"\nimplement = "codex"\n'
    )
    with pytest.raises(UsageError, match=r"workflows\.toml: \[build\] the implement slot needs"):
        plan(env.request("codex", "task"))


def test_a_workflows_file_linking_outside_the_config_dir_is_a_usage_error(
    env: Env, tmp_path: Path
) -> None:
    """§3.3: a workflows.toml shipped in a repository could name a write role."""
    planted = tmp_path / "repo-workflows.toml"
    planted.write_text('[build]\nshape = "review"\nreview = "codex"\n')
    (env.config / "workflows.toml").symlink_to(planted)
    with pytest.raises(UsageError, match="outside the configuration directory"):
        plan(env.request("codex", "task"))


# ── a workflow target (lot 3) ──────────────────────────────────────────────


RUN = "20260926T100000-aaaaaaaa"
OWNER = "20260926T090000-bbbbbbbb"
IMPLEMENT = {"kind": "workflow", "name": "build", "shape": "implement"}


def _workflows(env: Env) -> None:
    env.roles(
        '[implementer]\nprovider = "codex"\nwrite = true\n\n[reviewer]\nprovider = "claude"\n'
    )
    (env.config / "workflows.toml").write_text(
        '[build]\nshape = "implement"\nimplement = "implementer"\n\n'
        '[check]\nshape = "review"\nreview = "reviewer"\n'
    )


def test_a_workflow_target_plans_its_implement_role_on_the_template(env: Env) -> None:
    _workflows(env)
    planned = plan(env.request("build", "Add a flag."))
    assert planned.workflow is not None and planned.workflow.name == "build"
    assert planned.role.name == "implementer" and planned.role.write
    assert planned.task == "Add a flag." and planned.prompt == implement_prompt("Add a flag.")


def test_a_role_target_keeps_its_task_as_its_prompt(env: Env) -> None:
    planned = plan(env.request("codex", "Explain."))
    assert planned.workflow is None and planned.task == planned.prompt == "Explain."


@pytest.mark.parametrize(
    ("overrides", "flag"),
    [
        (Overrides(model="m"), "-m"),
        (Overrides(effort="high"), "--effort"),
        (Overrides(timeout=5.0), "--timeout"),
        (Overrides(context="none"), "--context"),
        (Overrides(context_parents=True), "--context-parents"),
        (Overrides(mcp="p"), "--mcp"),
        (Overrides(write=True), "--write"),
        (Overrides(shell=True), "--shell"),
        (Overrides(base_url="http://x"), "--base-url"),
        (Overrides(key_env="K"), "--key-env"),
    ],
)
def test_a_workflow_target_refuses_every_override(
    env: Env, overrides: Overrides, flag: str
) -> None:
    """§3.3, §3.9: a workflow runs its roles as declared; none of its options is a capability."""
    _workflows(env)
    with pytest.raises(UsageError, match=f"runs its roles as declared, so {flag} is refused"):
        plan(env.request("build", "task", overrides=overrides))


# ── a review target (lot 4) ────────────────────────────────────────────────


def _panel(env: Env) -> None:
    env.roles(
        '[implementer]\nprovider = "codex"\nwrite = true\n\n'
        '[reviewer]\nprovider = "claude"\n\n'
        '[reviewer-oc]\nprovider = "opencode"\n\n'
        '[judge]\nprovider = "codex"\n'
    )
    (env.config / "workflows.toml").write_text(
        '[build]\nshape = "implement"\nimplement = "implementer"\n\n'
        '[check]\nshape = "review"\nreview = "reviewer"\n\n'
        '[panel]\nshape = "review"\nreview = ["reviewer", "reviewer-oc"]\njudge = "judge"\n'
    )


def test_a_review_target_plans_every_slot_of_its_panel_in_order(
    env: Env, no_subprocess: list[object]
) -> None:
    """Spec §3.5: the reviewers, then the judge; plan() runs no git (§3.8.2)."""
    _panel(env)
    planned = plan(env.request("panel", "Look at the error paths."))
    assert planned.workflow is not None and planned.workflow.shape == "review"
    assert [(s.slot, s.role.name) for s in planned.panel] == [
        ("review", "reviewer"),
        ("review", "reviewer-oc"),
        ("judge", "judge"),
    ]
    assert [dict(s.models) for s in planned.panel] == [
        {"claude": "claude-default"},
        {"opencode": "oc-default"},
        {"codex": "codex-default"},
    ]
    assert planned.task == "Look at the error paths."
    assert not planned.role.write


@pytest.mark.parametrize("prompt", [None, "", "  \n"])
def test_a_review_without_a_prompt_reviews_the_change(env: Env, prompt: str | None) -> None:
    """Spec §3.9: a review's prompt is optional; on a terminal it is not refused."""
    _panel(env)
    planned = plan(env.request("check", prompt, stdin_is_tty=True))
    assert planned.task == REVIEW_DEFAULT_TASK == "Review this change."


def test_run_names_an_implement_run_and_its_lineage(env: Env, no_subprocess: list[object]) -> None:
    _panel(env)
    env.register(RUN, target=IMPLEMENT, lineage=OWNER)
    planned = plan(env.request("check", None, review_run=RUN))
    assert planned.reviews == RUN and planned.reviewed_lineage == OWNER


@pytest.mark.parametrize("given", [{"head": "HEAD~1"}, {"base": "main"}])
def test_run_excludes_head_and_base(env: Env, given: dict[str, str]) -> None:
    _panel(env)
    env.register(RUN, target=IMPLEMENT, lineage=OWNER)
    with pytest.raises(UsageError, match="--run excludes --head and --base"):
        plan(env.request("check", None, review_run=RUN, **given))  # type: ignore[arg-type]


def test_run_refuses_a_run_that_is_not_an_implement_run(env: Env) -> None:
    _panel(env)
    env.register(RUN, target={"kind": "provider", "name": "codex"}, lineage=None)
    with pytest.raises(UsageError, match=f"--run {RUN}: not an implement run"):
        plan(env.request("check", None, review_run=RUN))


@pytest.mark.parametrize(("run_id", "rule"), [("nope", "not a run id"), (RUN, f"no run {RUN}")])
def test_run_refuses_what_names_no_registered_run(env: Env, run_id: str, rule: str) -> None:
    _panel(env)
    with pytest.raises(UsageError, match=rule):
        plan(env.request("check", None, review_run=run_id))


@pytest.mark.parametrize(
    ("target", "fields", "rule"),
    [
        ("build", {"head": "HEAD"}, "--head needs a review workflow"),
        ("build", {"review_run": RUN}, "--run needs a review workflow"),
        ("codex", {"head": "HEAD"}, "--head needs a review workflow"),
        ("codex", {"review_run": RUN}, "--run needs a review workflow"),
        ("check", {"continue_run": RUN}, "--continue needs an implement workflow"),
    ],
)
def test_each_option_belongs_to_its_shape(
    env: Env, target: str, fields: dict[str, str], rule: str
) -> None:
    """Spec §3.9: --head and --run are the review's; --continue the implement's."""
    _panel(env)
    with pytest.raises(UsageError, match=rule):
        plan(env.request(target, "task", **fields))  # type: ignore[arg-type]


def test_a_review_refuses_every_override_too(env: Env) -> None:
    _panel(env)
    with pytest.raises(UsageError, match="runs its roles as declared, so -m is refused"):
        plan(env.request("check", None, overrides=Overrides(model="m")))


def test_an_implement_workflow_needs_a_task(env: Env) -> None:
    _workflows(env)
    with pytest.raises(UsageError, match="no prompt"):
        plan(env.request("build", None, stdin_is_tty=True))


def test_the_implement_template_counts_against_the_prompt_limit(env: Env) -> None:
    """§3.4: the size checked is the prompt the provider gets -- the template around the task."""
    env.roles('[implementer]\nprovider = "opencode"\nwrite = true\n')
    (env.config / "workflows.toml").write_text(
        '[build]\nshape = "implement"\nimplement = "implementer"\n'
    )
    limit = max_prompt_bytes("opencode")
    assert limit is not None
    task = "x" * (limit - 50)
    plan(env.request("opencode", task))
    with pytest.raises(UsageError, match="opencode: the prompt with its context exceeds"):
        plan(env.request("build", task))


def test_an_unknown_target_lists_the_workflows_too(env: Env) -> None:
    _workflows(env)
    with pytest.raises(UsageError, match=r"workflows, roles and providers: .*\bbuild\b"):
        plan(env.request("nobody", "task"))


def test_continue_needs_an_implement_workflow_target(env: Env) -> None:
    _workflows(env)
    with pytest.raises(UsageError, match="--continue needs an implement workflow"):
        plan(env.request("codex", "task", continue_run=RUN))


def test_continue_and_base_exclude_each_other(env: Env) -> None:
    _workflows(env)
    with pytest.raises(UsageError, match="--base and --continue exclude each other"):
        plan(env.request("build", "task", base="main", continue_run=RUN))


@pytest.mark.parametrize(("run_id", "rule"), [("nope", "not a run id"), (RUN, f"no run {RUN}")])
def test_continue_refuses_what_names_no_registered_run(env: Env, run_id: str, rule: str) -> None:
    _workflows(env)
    with pytest.raises(UsageError, match=rule):
        plan(env.request("build", "task", continue_run=run_id))


def test_continue_refuses_an_unreadable_entry(env: Env) -> None:
    _workflows(env)
    (env.state / "runs").mkdir(parents=True)
    (env.state / "runs" / f"{RUN}.json").write_text("{not json")
    with pytest.raises(UsageError, match="recover it by hand"):
        plan(env.request("build", "task", continue_run=RUN))


@pytest.mark.parametrize(
    ("target", "lineage"),
    [
        ({"kind": "provider", "name": "codex"}, None),
        ({"kind": "role", "name": "implementer"}, RUN),
        ({"kind": "workflow", "name": "check", "shape": "review"}, None),
    ],
    ids=["read-only-run", "role-write-run", "review-run"],
)
def test_continue_refuses_a_run_that_is_not_an_implement_run(
    env: Env, target: dict[str, str], lineage: str | None
) -> None:
    """§3.6: only a member of an implement lineage can be continued."""
    _workflows(env)
    env.register(RUN, target=target, lineage=lineage)
    with pytest.raises(UsageError, match=f"--continue {RUN}: not an implement run"):
        plan(env.request("build", "task", continue_run=RUN))


def test_continue_names_the_run_and_the_lineage_it_joins(
    env: Env, no_subprocess: list[object]
) -> None:
    """Read from the registry only: plan() starts no subprocess (§3.8.2)."""
    _workflows(env)
    env.register(RUN, target=IMPLEMENT, lineage=OWNER)
    planned = plan(env.request("build", "Fix it.", continue_run=RUN))
    assert planned.continues == RUN and planned.joins == OWNER


# ── output schema (0.5.3 lot 1) ─────────────────────────────────────────────

SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}
#: 0.5.3 Task 0's (f): a property absent from ``required``, which codex's API refuses.
NON_STRICT: dict[str, object] = {"type": "object", "properties": {"ok": {"type": "boolean"}}}


@pytest.mark.parametrize("target", ["openrouter", "openai-compat"])
def test_output_schema_is_refused_on_an_http_target(
    env: Env, target: str, no_subprocess: list[object]
) -> None:
    overrides = Overrides(base_url="http://x", key_env="K") if target == "openai-compat" else None
    with pytest.raises(UsageError, match=f"{target} cannot constrain"):
        plan(env.request(target, "task", overrides=overrides, output_schema=SCHEMA))


def test_an_output_schema_no_rail_can_take_is_a_usage_error(env: Env) -> None:
    with pytest.raises(UsageError, match="object-rooted"):
        plan(env.request("codex", "task", output_schema={"type": "array"}))


def test_output_schema_is_refused_for_a_review_target_too(env: Env) -> None:
    _panel(env)
    with pytest.raises(UsageError, match="--output-schema needs a provider or a role"):
        plan(env.request("check", "task", output_schema=SCHEMA))


def test_a_schema_on_claude_or_codex_is_planned(env: Env, no_subprocess: list[object]) -> None:
    env.roles('[pair]\nchain = ["codex", "claude"]\n')
    planned = plan(env.request("pair", "task", output_schema=SCHEMA))
    assert planned.request.output_schema == SCHEMA


def test_a_schema_codex_strict_mode_rejects_is_refused_before_any_run(
    env: Env, no_subprocess: list[object]
) -> None:
    """Measured (codex-cli 0.156.0): codex's API refuses such a schema only once the
    run started, in its event stream. A chain holding codex refuses it at plan time;
    claude, which is not strict, still takes it."""
    env.roles('[pair]\nchain = ["claude", "codex"]\n')
    with pytest.raises(UsageError, match=r"^codex cannot constrain .*'required' misses 'ok'"):
        plan(env.request("pair", "task", output_schema=NON_STRICT))
    with pytest.raises(UsageError, match=r"^codex cannot constrain"):
        plan(env.request("codex", "task", output_schema=NON_STRICT))
    assert plan(env.request("claude", "task", output_schema=NON_STRICT)).request.output_schema


def test_review_plans_pin_a_vendor_table_and_role_runs_do_not(env: Env) -> None:
    _panel(env)
    assert plan(env.request("panel", None)).vendor_table is not None
    assert plan(env.request("reviewer", "task")).vendor_table is None


def test_panel_members_use_frozen_models_for_links_roles_and_defaults(env: Env) -> None:
    env.roles(
        '[chain]\nchain = ["opencode:opencode-go/deepseek-v4-pro", "agy"]\n\n'
        '[role-model]\nprovider = "claude"\nmodel = "claude-role"\n\n'
        '[judge]\nprovider = "codex"\n'
    )
    (env.config / "models.toml").write_text('agy = "gemini-default"\ncodex = "codex-default"\n')
    (env.config / "workflows.toml").write_text(
        '[panel]\nshape = "review"\nreview = ["chain", "role-model"]\njudge = "judge"\n'
    )
    planned = plan(env.request("panel", None))
    members = panel_members(planned)
    assert [(member.slot, member.role) for member in members] == [
        ("review", "chain"),
        ("review", "role-model"),
        ("judge", "judge"),
    ]
    assert [[(link.provider, link.model) for link in member.links] for member in members] == [
        [("opencode", "opencode-go/deepseek-v4-pro"), ("agy", "gemini-default")],
        [("claude", "claude-role")],
        [("codex", "codex-default")],
    ]
    assert [[link.model for link in member.links] for member in members] == [
        [slot.models[link.provider] for link in slot.role.links] for slot in planned.panel
    ]
    (env.config / "models.toml").write_text('agy = "gemini-changed"\ncodex = "codex-changed"\n')
    assert panel_members(planned) == members


def test_a_non_toml_operator_vendor_table_refuses_review_planning(env: Env) -> None:
    _panel(env)
    (env.config / "vendors.toml").write_text("not = [toml")
    with pytest.raises(UsageError, match=r"vendors\.toml"):
        plan(env.request("panel", None))


# ── budget caps: resolution and admission (spec 2026-10-13 usage and budget §3.4, §3.5) ─


def _capped(env: Env, target: str, prompt: str | None = "task", **caps: object) -> Request:
    return replace(env.request(target, prompt), **caps)  # type: ignore[arg-type]


def _opencode_panel(env: Env, judge: str = "opencode") -> None:
    env.roles(
        '[oc-a]\nprovider = "opencode"\nmax_input_tokens = 5000\n\n'
        '[oc-b]\nprovider = "opencode"\nmax_input_tokens = 3000\nmax_cost = 0.75\n\n'
        f'[oc-judge]\nprovider = "{judge}"\nmax_input_tokens = 4000\n\n'
        '[oc-build]\nprovider = "opencode"\nwrite = true\n'
    )
    (env.config / "workflows.toml").write_text(
        '[oc-panel]\nshape = "review"\nreview = ["oc-a", "oc-b"]\njudge = "oc-judge"\n\n'
        '[oc-implement]\nshape = "implement"\nimplement = "oc-build"\n'
    )


def test_an_uncapped_run_plans_no_caps(env: Env) -> None:
    planned = plan(env.request("opencode", "task"))
    assert planned.caps == Caps() and not planned.caps.capped


def test_the_command_line_caps_the_run(env: Env, no_subprocess: list[object]) -> None:
    planned = plan(_capped(env, "opencode", max_cost=0.5, max_input_tokens=4000))
    assert planned.caps == Caps(max_cost=0.5, max_input_tokens=4000)


def test_a_roles_caps_apply_and_the_command_line_wins_even_when_larger(env: Env) -> None:
    env.roles('[oc]\nprovider = "opencode"\nmax_cost = 0.5\nmax_input_tokens = 4000\n')
    assert plan(env.request("oc", "task")).caps == Caps(max_cost=0.5, max_input_tokens=4000)
    planned = plan(_capped(env, "oc", max_input_tokens=9000))
    assert planned.caps == Caps(max_cost=0.5, max_input_tokens=9000)


def test_a_workflow_takes_the_smallest_cap_of_its_roles(env: Env) -> None:
    _opencode_panel(env)
    planned = plan(env.request("oc-panel", None))
    assert planned.caps == Caps(max_cost=0.75, max_input_tokens=3000)


def test_caps_are_run_options_a_workflow_accepts(env: Env) -> None:
    _opencode_panel(env)
    planned = plan(_capped(env, "oc-implement", "Add a flag.", max_cost=0.25))
    assert planned.caps == Caps(max_cost=0.25)
    reviewed = plan(_capped(env, "oc-panel", None, max_input_tokens=100))
    assert reviewed.caps == Caps(max_cost=0.75, max_input_tokens=100)


@pytest.mark.parametrize(
    ("field_name", "value", "flag"),
    [
        ("max_cost", 0, "--max-cost"),
        ("max_cost", -1.0, "--max-cost"),
        ("max_cost", float("nan"), "--max-cost"),
        ("max_cost", float("inf"), "--max-cost"),
        ("max_cost", True, "--max-cost"),
        ("max_input_tokens", 0, "--max-input-tokens"),
        ("max_input_tokens", -5, "--max-input-tokens"),
        ("max_input_tokens", 1.5, "--max-input-tokens"),
        ("max_input_tokens", True, "--max-input-tokens"),
    ],
)
def test_an_invalid_cap_is_a_usage_error(
    env: Env, no_subprocess: list[object], field_name: str, value: object, flag: str
) -> None:
    with pytest.raises(UsageError, match=f"^{flag} .*: must be a"):
        plan(_capped(env, "opencode", **{field_name: value}))


@pytest.mark.parametrize("provider", ["claude", "codex", "agy", "vibe", "openrouter"])
def test_a_cap_on_a_rail_that_cannot_enforce_it_is_refused_before_anything(
    env: Env, no_subprocess: list[object], provider: str
) -> None:
    (env.config / "models.toml").write_text(
        'claude = "c"\ncodex = "x"\nagy = "g"\nvibe = "v"\nopenrouter = "o"\n'
    )
    with pytest.raises(
        UsageError,
        match=(
            f"^role {provider}, link {provider}: max_input_tokens 1000 cannot be enforced: "
            f"{provider} reports no usage during a run; nothing ran$"
        ),
    ):
        plan(_capped(env, provider, max_input_tokens=1000))
    with pytest.raises(UsageError, match=f"max_cost 0.5 USD cannot be enforced: {provider}"):
        plan(_capped(env, provider, max_cost=0.5))
    assert not env.state.exists(), "nothing registered"


def test_a_chain_whose_second_link_only_is_unable_is_refused(env: Env) -> None:
    env.roles('[pair]\nchain = ["opencode", "codex"]\n')
    with pytest.raises(UsageError, match="^role pair, link codex: max_cost 1 USD cannot be"):
        plan(_capped(env, "pair", max_cost=1.0))
    assert plan(env.request("pair", "task")).caps == Caps(), "uncapped, the chain still plans"


def test_a_role_cap_on_a_rail_that_cannot_enforce_it_refuses_every_run_of_it(env: Env) -> None:
    env.roles('[rev]\nprovider = "codex"\nmax_cost = 1\n')
    with pytest.raises(UsageError, match="^role rev, link codex: max_cost 1 USD cannot be"):
        plan(env.request("rev", "task"))


def test_a_review_whose_judge_only_is_unable_is_refused(
    env: Env, no_subprocess: list[object]
) -> None:
    _opencode_panel(env, judge="codex")
    with pytest.raises(UsageError, match="^role oc-judge, link codex: .* cannot be enforced"):
        plan(env.request("oc-panel", None))


def test_an_implement_workflow_on_an_unable_rail_is_refused(env: Env) -> None:
    _workflows(env)
    with pytest.raises(UsageError, match="^role implementer, link codex: max_input_tokens 10 "):
        plan(_capped(env, "build", "Add a flag.", max_input_tokens=10))
