"""Usage read from a rail's own stream (spec 2026-10-13 usage and budget §3.1).

A rail reads usage only from the bytes its child wrote in THIS invocation: the offset of
the stream is recorded when the child is launched, and a rail that returns before
launching leaves it unset, so a reused log can never lend an earlier run's usage.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class StreamMark:
    """Where this invocation's bytes start in a stream file; ``None`` until its child launched."""

    offset: int | None = None

    def read(self, path: Path | None) -> str | None:
        """The bytes from the mark on, decoded with replacement; ``None`` when nothing
        launched or the file cannot be read. Never raises."""
        if self.offset is None or path is None:
            return None
        try:
            with path.open("rb") as stream:
                stream.seek(self.offset)
                return stream.read().decode("utf-8", errors="replace")
        except OSError:
            return None


__all__ = ["StreamMark"]
