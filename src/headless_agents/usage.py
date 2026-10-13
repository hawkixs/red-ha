"""Per-invocation stream offsets used to measure provider usage."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class StreamMark:
    """Where this invocation's bytes start in a stream file; ``None`` until its child launched."""

    offset: int | None = None

    def read(self, path: Path | None) -> str | None:
        """Read bytes from this mark, or ``None`` when unlaunched or unreadable."""
        if self.offset is None or path is None:
            return None
        try:
            with path.open("rb") as stream:
                stream.seek(self.offset)
                return stream.read().decode("utf-8", errors="replace")
        except OSError:
            return None


__all__ = ["StreamMark"]
