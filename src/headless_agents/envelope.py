"""Unwrapping CLI envelopes. PURE: ``str -> Reply``, no process.

The three subscription CLIs can render their answer inside a JSON envelope
that also carries their consumption. That is what lets a consumer DISPLAY
which model and which reasoning actually played, rather than normalising it.

What each one declares, read off real captures:

    claude   ``--output-format json``: ``result``, ``modelUsage`` keyed by the
             model that actually played, ``total_cost_usd``. The ONLY one of
             the three to name its model and give a cost -- and the only one
             NOT to separate its reasoning, which is folded into
             ``outputTokens``.
    codex    ``--json``: JSONL. The text sits in an ``item.completed`` of type
             ``agent_message``, the consumption in ``turn.completed`` with
             ``reasoning_output_tokens`` apart. No model name.
    agy      ``--output-format json``: one object, ``response`` plus
             ``usage.thinking_tokens``; ``--output-format stream-json``: JSONL
             whose ``result`` event carries the same two. No model name.

RESILIENCE RULE, non-negotiable: an unreadable envelope yields the RAW output,
never an exception. Instrumentation must not be a condition for running. The
CLIs update themselves; the day a field is renamed, the caller must keep
running while losing its measurement.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass

from .result import TokenUsage


@dataclass(frozen=True, kw_only=True)
class Reply:
    """What a CLI answered, and what it said it consumed."""

    text: str
    model_reported: str | None = None
    tokens: TokenUsage | None = None
    cost_usd: float | None = None


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _text(data: Mapping[str, object], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) else None


def _int(data: Mapping[str, object], key: str) -> int | None:
    value = data.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _number(data: Mapping[str, object], key: str) -> float | None:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _load_object(stdout: str) -> Mapping[str, object] | None:
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, RecursionError, ValueError):
        return None
    return data if isinstance(data, Mapping) else None


def _jsonl(stdout: str) -> list[Mapping[str, object]]:
    events: list[Mapping[str, object]] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line.startswith("{"):
            continue  # the CLIs interleave plain-text warnings
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, RecursionError, ValueError):
            continue
        if isinstance(event, Mapping):
            events.append(event)
    return events


def _claude_model_usage(detail: Mapping[str, object]) -> TokenUsage:
    return TokenUsage(
        input=_int(detail, "inputTokens"),
        output=_int(detail, "outputTokens"),
        cached=_int(detail, "cacheReadInputTokens"),
    )


_ModelUsage = tuple[tuple[str, Mapping[str, object]], ...]


@dataclass(frozen=True, kw_only=True)
class ClaudeEnvelope:
    """What claude's ``--output-format json`` envelope carries, extracted once.

    The shared extraction step (spec 2026-10-13 usage §3.1): :func:`unwrap_claude` and the
    claude rail both read it, each with its own aggregation.
    """

    text: str | None
    cost_usd: float | None
    models: _ModelUsage
    usage: Mapping[str, object]


def extract_claude(stdout: str) -> ClaudeEnvelope | None:
    """The envelope in ``stdout``; ``None`` when stdout is not a JSON object.

    ``{}`` IS an envelope -- one without a result, a cost or a usage: the claude rail
    then answers nothing (spec 2026-10-13 usage §3.1), and :func:`unwrap_claude` still
    returns it verbatim, as it always did.
    """
    data = _load_object(stdout)
    if data is None:
        return None
    return ClaudeEnvelope(
        text=_text(data, "result"),
        cost_usd=_number(data, "total_cost_usd"),
        models=tuple(
            (model, detail)
            for model, raw in _mapping(data.get("modelUsage")).items()
            if isinstance(model, str) and (detail := _mapping(raw))
        ),
        usage=_mapping(data.get("usage")),
    )


def _total(models: _ModelUsage, key: str) -> int | None:
    values = [_int(detail, key) for _, detail in models]
    if not values or any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def claude_usage(envelope: ClaudeEnvelope) -> TokenUsage | None:
    """The claude rail's aggregation: the whole invocation, every model summed.

    ``input`` counts cache reads and cache creation; ``fresh`` is ``input - cached`` (cache
    creation is fresh input); ``thinking`` sums ``thinkingTokens`` where reported and is
    never added to ``output``. ``None`` without ``modelUsage``.
    """
    models = envelope.models
    if not models:
        return None
    given = _total(models, "inputTokens")
    read = _total(models, "cacheReadInputTokens")
    created = _total(models, "cacheCreationInputTokens")
    total_input = (
        given + read + created
        if given is not None and read is not None and created is not None
        else None
    )
    reported = [
        value for _, detail in models if (value := _int(detail, "thinkingTokens")) is not None
    ]
    return TokenUsage(
        input=total_input,
        output=_total(models, "outputTokens"),
        fresh=total_input - read if total_input is not None and read is not None else None,
        cached=read,
        thinking=sum(reported) if reported else None,
    )


def unwrap_claude(stdout: str, *, expected_model: str | None = None) -> Reply:
    """``modelUsage`` is keyed BY the model that played -- the only source of its name.

    ``outputTokens`` is read from ``modelUsage`` and not from
    ``usage.output_tokens``: both exist and differ (4709 against 4692 on the
    reference capture), and it is the ``modelUsage`` one that is attributed to
    the named model. The global ``usage`` is the fallback when no single
    attribution is certain.
    """
    envelope = extract_claude(stdout)
    if envelope is None:
        return Reply(text=stdout)
    text = envelope.text
    cost = envelope.cost_usd
    measured = list(envelope.models)
    chosen = measured if len(measured) == 1 else []
    if expected_model is not None and len(measured) > 1:
        chosen = [
            (model, detail)
            for model, detail in measured
            if model == expected_model or _text(detail, "canonicalModel") == expected_model
        ]
    if len(chosen) == 1:
        model, detail = chosen[0]
        return Reply(
            text=stdout if text is None else text,
            model_reported=model,
            tokens=_claude_model_usage(detail),
            cost_usd=cost,
        )
    usage = envelope.usage
    tokens = (
        TokenUsage(input=_int(usage, "input_tokens"), output=_int(usage, "output_tokens"))
        if usage
        else None
    )
    return Reply(text=stdout if text is None else text, tokens=tokens, cost_usd=cost)


def _codex_turn_usage(usage: Mapping[str, object]) -> TokenUsage | None:
    """One ``turn.completed`` usage block; ``None`` when it is empty."""
    if not usage:
        return None
    input_tokens = _int(usage, "input_tokens")
    cached = _int(usage, "cached_input_tokens")
    fresh = input_tokens - cached if input_tokens is not None and cached is not None else None
    return TokenUsage(
        input=input_tokens,
        output=_int(usage, "output_tokens"),
        fresh=fresh,
        cached=cached,
        thinking=_int(usage, "reasoning_output_tokens"),
    )


def unwrap_codex(stdout: str) -> Reply:
    """JSONL: the text and the consumption live on DIFFERENT lines.

    Neither the first nor the last line will do -- the stream starts with
    ``turn.started`` and ends with ``turn.completed``, which carries no text.
    So we search by type, not by position. ``fresh`` follows the Codex
    convention: ``cached_input_tokens`` is a SUBSET of ``input_tokens``.
    """
    text: str | None = None
    tokens: TokenUsage | None = None
    for event in _jsonl(stdout):
        item = _mapping(event.get("item"))
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            read = _text(item, "text")
            if read is not None:
                text = read
        elif event.get("type") == "turn.completed":
            turn = _codex_turn_usage(_mapping(event.get("usage")))
            if turn is not None:
                tokens = turn
    return Reply(text=stdout if text is None else text, tokens=tokens)


def codex_usage(stdout: str) -> TokenUsage | None:
    """The codex rail's reader: the usage of the ONE ``turn.completed`` of the stream.

    Measured 2026-10-13 on 1,029 logs: ``codex exec`` writes exactly one per run. A stream
    with more is not measured until a recording shows what they mean -- never a guessed sum.
    """
    turns = [event for event in _jsonl(stdout) if event.get("type") == "turn.completed"]
    if len(turns) != 1:
        return None
    return _codex_turn_usage(_mapping(turns[0].get("usage")))


def _agy_usage(usage: Mapping[str, object]) -> TokenUsage | None:
    if not usage:
        return None
    return TokenUsage(
        input=_int(usage, "input_tokens"),
        output=_int(usage, "output_tokens"),
        cached=_int(usage, "cache_read_tokens"),
        thinking=_int(usage, "thinking_tokens"),
    )


def _agy_result(stdout: str) -> Mapping[str, object] | None:
    """The ``result`` of the LAST ``result`` event of a stream-json output, if any."""
    result: Mapping[str, object] | None = None
    for event in _jsonl(stdout):
        if event.get("event") == "result":
            result = _mapping(event.get("result"))
    return result


def unwrap_agy(stdout: str) -> Reply:
    """One object (``--output-format json``) or a stream whose ``result`` event
    carries the same ``response`` and ``usage`` (``stream-json``)."""
    data = _load_object(stdout)
    if data is None:
        data = _agy_result(stdout)
        if data is None:
            return Reply(text=stdout)
    text = _text(data, "response")
    return Reply(
        text=stdout if text is None else text, tokens=_agy_usage(_mapping(data.get("usage")))
    )


def agy_usage(stdout: str) -> TokenUsage | None:
    """The agy rail's reader: the final ``result`` event of its stream-json output.

    agy's ``input_tokens`` EXCLUDES cache reads (measured on 15 real result events:
    ``total_tokens = input_tokens + output_tokens`` every time, ``cache_read_tokens`` up to
    15x ``input_tokens``), so ``input`` is their sum, like the other rails (spec amendment
    A3).
    """
    result = _agy_result(stdout)
    if result is None:
        return None
    usage = _mapping(result.get("usage"))
    if not usage:
        return None
    fresh = _int(usage, "input_tokens")
    cached = _int(usage, "cache_read_tokens")
    return TokenUsage(
        input=fresh + cached if fresh is not None and cached is not None else None,
        output=_int(usage, "output_tokens"),
        fresh=fresh,
        cached=cached,
        thinking=_int(usage, "thinking_tokens"),
    )


def unwrap(provider: str, stdout: str, *, expected_model: str | None = None) -> Reply:
    """Dispatch by provider name; ``text`` and unknown providers are the identity."""
    try:
        if provider == "claude":
            return unwrap_claude(stdout, expected_model=expected_model)
        if provider == "codex":
            return unwrap_codex(stdout)
        if provider == "agy":
            return unwrap_agy(stdout)
    except Exception:  # noqa: BLE001 -- the resilience net at the external boundary
        pass
    return Reply(text=stdout)
