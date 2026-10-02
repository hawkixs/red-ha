"""``ha init``: the role and workflow presets (spec 0.5.4 §3.7, 0.5.3 spec lot 6 / D2).

The names are the ones red-skills' ha-delegate uses (decision 35521a0d). No model is
named: the package hard-codes no model name, so each tier is a commented line for the
operator to fill, or ``models.toml`` decides.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

ROLES_TOML: Final = """\
# Role presets written by `ha init`. No model is named: put each tier's model on the
# commented line, or let models.toml decide. Edit freely; `ha init` never overwrites.

[judge]
provider = "codex"
effort   = "high"
context  = "none"
timeout  = 1800
# model = "<your strongest codex model>"

[closure]
provider = "codex"
effort   = "high"
context  = "global"
timeout  = 1200
# model = "<your fast codex model>"

[builder]
provider = "codex"
effort   = "medium"
write    = true
context  = "full"
timeout  = 1800
# model = "<your fast codex model>"

[builder-deep]
provider = "codex"
effort   = "high"
write    = true
context  = "full"
timeout  = 3600
# model = "<your strongest codex model>"

[pr-judge-agy]
provider = "agy"
context  = "none"
timeout  = 1800

[reviewer-agy]
provider = "agy"
context  = "global"
timeout  = 1800
"""

WORKFLOWS_TOML: Final = """\
# Workflow presets written by `ha init`; they name the roles of roles.toml.

[build]
shape     = "implement"
implement = "builder"

[build-deep]
shape     = "implement"
implement = "builder-deep"

[review-agy]
shape  = "review"
review = "reviewer-agy"

[review-codex]
shape  = "review"
review = "closure"
"""

PRESET_FILES: Final[tuple[tuple[str, str], ...]] = (
    ("roles.toml", ROLES_TOML),
    ("workflows.toml", WORKFLOWS_TOML),
)


def write_presets(directory: Path) -> list[Path]:
    """Write both files into ``directory``; refuse before writing anything if one exists."""
    targets = [directory / name for name, _ in PRESET_FILES]
    for target in targets:
        if os.path.lexists(target):
            raise FileExistsError(f"{target} exists; ha init never overwrites")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    for target, (_, text) in zip(targets, PRESET_FILES, strict=True):
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
    return targets
