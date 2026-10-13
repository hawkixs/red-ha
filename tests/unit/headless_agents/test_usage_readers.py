"""The rails' usage readers (spec 2026-10-13 usage and budget §3.1, §4 PR1).

Each reader is pinned on a recorded output; the public ``unwrap_*`` helpers are pinned
unchanged on the same fixtures (spec §5: their results do not move).
"""

from __future__ import annotations

import json
from pathlib import Path

from headless_agents.envelope import (
    Reply,
    agy_usage,
    claude_usage,
    codex_usage,
    extract_claude,
    unwrap_agy,
    unwrap_claude,
    unwrap_codex,
)
from headless_agents.providers import opencode
from headless_agents.result import TokenUsage

FIXTURES = Path(__file__).parent / "fixtures"
STRUCTURED = FIXTURES / "structured"
USAGE = FIXTURES / "usage"
TOOL_COUNTS = FIXTURES / "tool_counts"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestClaudeUsage:
    def test_the_success_envelope_sums_cache_creation_into_input(self) -> None:
        envelope = extract_claude(_read(STRUCTURED / "claude-2.1.283.success.json"))
        assert envelope is not None
        assert envelope.text == '{"ok":true}'
        assert envelope.cost_usd == 0.019488
        assert claude_usage(envelope) == TokenUsage(
            input=9499, output=100, fresh=9499, cached=0, thinking=43
        )

    def test_the_max_turns_envelope_measures_without_a_result(self) -> None:
        envelope = extract_claude(_read(STRUCTURED / "claude-2.1.283.max-turns.json"))
        assert envelope is not None
        assert envelope.text is None
        assert envelope.cost_usd == 0.025328600000000003
        assert claude_usage(envelope) == TokenUsage(
            input=30004, output=531, fresh=10368, cached=19636, thinking=350
        )

    def test_two_models_are_summed_and_thinking_counts_where_reported(self) -> None:
        envelope = extract_claude(_read(USAGE / "claude.two-models.synthetic.json"))
        assert envelope is not None
        assert envelope.cost_usd == 0.020488
        assert claude_usage(envelope) == TokenUsage(
            input=10504, output=120, fresh=9504, cached=1000, thinking=43
        )

    def test_thinking_is_none_when_no_model_reports_it(self) -> None:
        single = json.loads(_read(STRUCTURED / "claude-2.1.283.max-turns.json"))
        for detail in single["modelUsage"].values():
            del detail["thinkingTokens"]
        envelope = extract_claude(json.dumps(single))
        assert envelope is not None
        usage = claude_usage(envelope)
        assert usage is not None and usage.thinking is None and usage.output == 531
        double = json.loads(_read(USAGE / "claude.two-models.synthetic.json"))
        for detail in double["modelUsage"].values():
            detail.pop("thinkingTokens", None)
        envelope = extract_claude(json.dumps(double))
        assert envelope is not None
        usage = claude_usage(envelope)
        assert usage is not None and usage.thinking is None and usage.output == 120

    def test_the_recorded_envelope_without_a_schema(self) -> None:
        raw = _read(USAGE / "claude.no-schema.json")
        document = json.loads(raw)
        models = list(document["modelUsage"].values())
        read = sum(m["cacheReadInputTokens"] for m in models)
        given = sum(
            m["inputTokens"] + m["cacheReadInputTokens"] + m["cacheCreationInputTokens"]
            for m in models
        )
        thinking = [m["thinkingTokens"] for m in models if "thinkingTokens" in m]
        envelope = extract_claude(raw)
        assert envelope is not None
        assert "structured_output" not in document
        assert envelope.text == document["result"]
        assert envelope.cost_usd == document["total_cost_usd"]
        assert given > 0
        assert claude_usage(envelope) == TokenUsage(
            input=given,
            output=sum(m["outputTokens"] for m in models),
            fresh=given - read,
            cached=read,
            thinking=sum(thinking) if thinking else None,
        )

    def test_what_is_not_an_envelope_extracts_nothing(self) -> None:
        assert extract_claude("plain text, not json") is None
        assert extract_claude("[1, 2]") is None
        assert extract_claude("") is None
        empty = extract_claude("{}")
        assert empty is not None and empty.text is None and empty.cost_usd is None
        assert claude_usage(empty) is None
        assert unwrap_claude("{}") == Reply(text="{}")
        envelope = extract_claude('{"result": "x"}')
        assert envelope is not None and envelope.text == "x"
        assert claude_usage(envelope) is None

    def test_the_public_unwrap_is_unchanged_on_the_fixtures(self) -> None:
        success = _read(STRUCTURED / "claude-2.1.283.success.json")
        assert unwrap_claude(success) == Reply(
            text='{"ok":true}',
            model_reported="claude-haiku-4-5-20251001",
            tokens=TokenUsage(input=10, output=100, cached=0),
            cost_usd=0.019488,
        )
        max_turns = _read(STRUCTURED / "claude-2.1.283.max-turns.json")
        assert unwrap_claude(max_turns) == Reply(
            text=max_turns,
            model_reported="claude-haiku-4-5-20251001",
            tokens=TokenUsage(input=26, output=531, cached=19636),
            cost_usd=0.025328600000000003,
        )
        two = _read(USAGE / "claude.two-models.synthetic.json")
        assert unwrap_claude(two) == Reply(
            text='{"ok":true}', tokens=TokenUsage(input=10, output=100), cost_usd=0.020488
        )


RECORDED_CODEX = TokenUsage(input=108250, output=2373, fresh=19418, cached=88832, thinking=1814)


class TestCodexUsage:
    def test_the_recorded_stream(self) -> None:
        stream = _read(TOOL_COUNTS / "codex.events.jsonl")
        assert codex_usage(stream) == RECORDED_CODEX
        assert unwrap_codex(stream) == Reply(text="<redacted>", tokens=RECORDED_CODEX)

    def test_two_completed_turns_are_not_measured(self) -> None:
        second = {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 1,
                "cached_input_tokens": 0,
                "output_tokens": 1,
                "reasoning_output_tokens": 0,
            },
        }
        stream = _read(TOOL_COUNTS / "codex.events.jsonl") + json.dumps(second) + "\n"
        assert codex_usage(stream) is None
        # The public helper keeps its own rule: the last turn wins.
        assert unwrap_codex(stream).tokens == TokenUsage(
            input=1, output=1, fresh=1, cached=0, thinking=0
        )

    def test_a_failed_turn_after_the_completed_one_keeps_its_usage(self) -> None:
        failed = {"type": "turn.failed", "error": {"message": "boom"}}
        stream = _read(TOOL_COUNTS / "codex.events.jsonl") + json.dumps(failed) + "\n"
        assert codex_usage(stream) == RECORDED_CODEX

    def test_no_completed_turn_or_an_empty_usage_is_not_measured(self) -> None:
        assert codex_usage("") is None
        assert codex_usage('{"type": "turn.started"}\n') is None
        assert codex_usage('{"type": "turn.completed"}\n') is None
        assert codex_usage('{"type": "turn.completed", "usage": {}}\n') is None


RECORDED_AGY = TokenUsage(input=133605, output=9307, cached=634901, thinking=5197)
# The rail's normalisation (spec A3): agy's input_tokens excludes cache reads.
RECORDED_AGY_RAIL = TokenUsage(
    input=768506, output=9307, fresh=133605, cached=634901, thinking=5197
)


class TestAgyUsage:
    def test_the_recorded_stream(self) -> None:
        stream = _read(TOOL_COUNTS / "agy.events.jsonl")
        assert agy_usage(stream) == RECORDED_AGY_RAIL
        assert unwrap_agy(stream) == Reply(text="<redacted>", tokens=RECORDED_AGY)

    def test_input_adds_cache_reads_only_when_both_are_measured(self) -> None:
        line = '{"event": "result", "result": {"usage": {"input_tokens": 7}}}\n'
        assert agy_usage(line) == TokenUsage(input=None, fresh=7)

    def test_a_stream_without_a_measured_result_is_not_measured(self) -> None:
        assert agy_usage("") is None
        assert agy_usage('{"event": "init"}\n') is None
        assert agy_usage('{"event": "result", "result": {"response": "x"}}\n') is None


class TestReferenceRails:
    def test_opencode_counts_a_re_emitted_part_once(self, tmp_path: Path) -> None:
        recorded = _read(TOOL_COUNTS / "opencode.events.jsonl")
        once = tmp_path / "once.jsonl"
        once.write_text(recorded, encoding="utf-8")
        last = [line for line in recorded.splitlines() if '"step_finish"' in line][-1]
        twice = tmp_path / "twice.jsonl"
        twice.write_text(recorded + last + "\n", encoding="utf-8")
        tokens, cost = opencode.telemetry(once)
        assert opencode.telemetry(twice) == (tokens, cost)
        assert tokens is not None and tokens.input is not None
        assert tokens.fresh is not None and tokens.cached is not None
        assert tokens.input == tokens.fresh + tokens.cached
