"""File operations: load and save typed Plan objects from PLAN.md on disk.

This module is the I/O boundary for the planfile library. Pure parsing
and rendering live in :mod:`bob_tools.planfile.parser` and
:mod:`bob_tools.planfile.renderer`; everything that touches the
filesystem lives here so the rest of the library can stay
side-effect-free and easy to test.

``save`` writes atomically: the new content is written to a sibling
tempfile, ``fsync``'d, and renamed over the destination so a crash
between write and rename never leaves a half-written PLAN.md. The
Stage 6 ``update`` helper with advisory file locking is not yet
implemented here; this module provides only the minimal ``load`` and
``save`` surface that Stage 7's CLI requires.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from bob_tools.planfile.model import Plan
from bob_tools.planfile.parser import parse_plan
from bob_tools.planfile.renderer import render_plan


def load(path: Path) -> Plan:
    """Read ``path`` and return the parsed :class:`Plan`.

    Errors from :func:`bob_tools.planfile.parser.parse_plan` propagate
    unchanged. The ``source_path`` on the returned Plan is set to
    ``path`` so subsequent error messages can name the file.
    """
    text = path.read_text()
    return parse_plan(text, source_path=path)


def save(path: Path, plan: Plan) -> None:
    """Atomically write ``plan`` to ``path`` in canonical form.

    Renders the plan, writes the bytes to a tempfile in the same
    directory, ``fsync``s the file descriptor, then ``os.replace``s the
    tempfile over ``path``. A crash between the write and the rename
    leaves the original file intact; a crash after the rename leaves
    the new file intact. The tempfile is removed on any pre-rename
    failure so failed writes do not litter the directory.
    """
    text = render_plan(plan)
    directory = path.parent if path.parent != Path("") else Path(".")
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(directory),
    )
    try:
        with os.fdopen(fd, "w") as fp:
            fp.write(text)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp_name, str(path))
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def update(path: Path, operation: object) -> Plan:  # pragma: no cover - stub
    """Placeholder for the Stage 6 safe-mutation entry point.

    The full ``update`` contract (load, advisory lock, re-parse to
    detect concurrent edits, apply ``operation``, save) is deferred.
    The CLI in Stage 7 invokes ``load`` and ``save`` directly rather
    than going through this helper, so leaving it as a stub does not
    block Stage 7. Raises :class:`NotImplementedError` to make the gap
    explicit if anything else tries to call it.
    """
    raise NotImplementedError(
        "update() is deferred; see NOTES.md entry for Stage 6/Stage 7."
    )
