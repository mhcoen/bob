"""Migration detection for old-format duplo projects."""

from __future__ import annotations

import re
import sys
from pathlib import Path

_MIGRATION_MESSAGE = """\
This project predates the SPEC.md / ref/ redesign. Migrate manually:

  1. Create a ref/ directory:  mkdir ref
  2. Move reference files into ref/. Reference files are images
     (.png .jpg .gif .webp), videos (.mp4 .mov .webm .avi), and
     PDFs that aren't part of your source code.
  3. Author a SPEC.md by hand. Use SPEC-template.md (in the duplo
     repository) as a starting point. At minimum, fill in:
     - ## Purpose: one or two sentences
     - ## Architecture: your platform/language stack
     - ## Sources: add the URL from .duplo/product.json if any
     - ## References: add an entry for each file you moved to ref/
  4. Run `duplo` again.

Your existing PLAN.md, .duplo/duplo.json, and source code are
unchanged. Nothing has been moved or modified by duplo."""


def _check_migration(target_dir: Path) -> None:
    """Print migration instructions and exit if *target_dir* needs migration."""
    if needs_migration(target_dir):
        print(_MIGRATION_MESSAGE)
        sys.exit(1)


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
