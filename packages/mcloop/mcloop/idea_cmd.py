"""Subcommand for appending ideas to IDEAS.md."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

_HEADER = """\
# Ideas

A flat scratchpad for ideas not yet ready to become PLAN.md tasks.
Add anything here — feature sketches, half-baked thoughts, things to
explore later. McLoop does not read or modify this file during runs.
Use `mcloop idea "some text"` to append from the command line.
"""


def _cmd_idea(project_dir: Path, text: str) -> None:
    """Append a timestamped idea to IDEAS.md, creating it if needed."""
    ideas_path = project_dir / "IDEAS.md"
    if not ideas_path.exists():
        ideas_path.write_text(_HEADER)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    line = f"- [{stamp}] {text}\n"

    with ideas_path.open("a") as f:
        f.write(line)
