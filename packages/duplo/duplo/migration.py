"""Migration detection for old-format duplo projects."""

from __future__ import annotations

import re
from pathlib import Path


def needs_migration(target_dir: Path) -> bool:
    """Return True if *target_dir* is an old-format duplo project.

    A project needs migration when ``.duplo/duplo.json`` exists but no
    valid new-format ``SPEC.md`` is present.  "New-format" is detected
    by two independent signals (either is sufficient):

    1. Marker string ``"How the pieces fit together:"`` in the spec.
    2. An ``## Sources`` heading (``^## Sources\\s*$``).
    """
    duplo_json = target_dir / ".duplo" / "duplo.json"
    spec = target_dir / "SPEC.md"

    if not duplo_json.exists():
        return False
    if not spec.exists():
        return True
    spec_text = spec.read_text()
    # Either signal is sufficient to declare the spec new-format.
    if "How the pieces fit together:" in spec_text:
        return False
    if re.search(r"^## Sources\s*$", spec_text, re.MULTILINE):
        return False
    return True
