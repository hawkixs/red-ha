"""``StreamMark``: only this invocation's bytes are read (spec 2026-10-13 usage §3.1)."""

from __future__ import annotations

from pathlib import Path

from headless_agents.usage import StreamMark


def test_an_unlaunched_mark_reads_nothing(tmp_path: Path) -> None:
    log = tmp_path / "events.jsonl"
    log.write_text("EARLIER RUN\n", encoding="utf-8")
    assert StreamMark().read(log) is None


def test_a_mark_reads_from_its_offset(tmp_path: Path) -> None:
    log = tmp_path / "events.jsonl"
    log.write_text("EARLIER\nTHIS\n", encoding="utf-8")
    assert StreamMark(offset=8).read(log) == "THIS\n"
    assert StreamMark(offset=0).read(log) == "EARLIER\nTHIS\n"


def test_a_missing_file_reads_nothing(tmp_path: Path) -> None:
    assert StreamMark(offset=0).read(tmp_path / "absent") is None
    assert StreamMark(offset=0).read(None) is None
