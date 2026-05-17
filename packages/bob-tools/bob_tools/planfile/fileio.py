"""File operations: load, save, and update typed Plan objects on disk.

This module is the I/O boundary for the planfile library. Pure parsing
and rendering live in :mod:`bob_tools.planfile.parser` and
:mod:`bob_tools.planfile.renderer`; everything that touches the
filesystem lives here so the rest of the library can stay
side-effect-free and easy to test.

``save`` writes atomically: the new content is written to a sibling
tempfile, ``fsync``'d, and renamed over the destination so a crash
between write and rename never leaves a half-written PLAN.md. ``save``
also holds an advisory exclusive ``fcntl.flock`` on a sidecar lock
file for the duration of the write, so a concurrent ``save`` or
``update`` cannot interleave. ``update`` is the safe-mutation entry
point for tools that race with humans: it loads, locks, re-reads to
detect concurrent external modification (raising
:class:`ConcurrentUpdateError` if the bytes on disk changed between
the unlocked load and the lock acquisition), applies the caller's
``operation``, saves while holding the same lock, and returns the new
Plan.

The lock is a separate sidecar file (``<path>.lock``) opened
``O_CREAT|O_RDWR`` so locking works whether or not the target file
exists yet and survives the ``os.replace`` that swaps a freshly
written tempfile over the target. ``fcntl.flock`` is advisory: a
process that does not call into this module can still write the file
without observing the lock, which is precisely the case
``ConcurrentUpdateError`` exists to surface.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

from bob_tools.planfile.model import Plan
from bob_tools.planfile.parser import parse_plan
from bob_tools.planfile.renderer import render_plan


class ConcurrentUpdateError(Exception):
    """Raised by :func:`update` when the file changed between load and lock.

    Carries the path so callers writing retry loops can decide whether
    to restart their operation against the new on-disk content. The
    bytes-level comparison performed by :func:`update` catches any
    external modification, including ones that produce a parse-
    equivalent tree — the rule is conservative on purpose because a
    tool that races with a human editor should defer rather than
    overwrite.
    """

    def __init__(self, path: Path) -> None:
        super().__init__(
            f"{path}: external modification detected between load and lock "
            "acquisition; retry against the current on-disk content"
        )
        self.path = path


def load(path: Path) -> Plan:
    """Read ``path`` and return the parsed :class:`Plan`.

    Errors from :func:`bob_tools.planfile.parser.parse_plan` propagate
    unchanged. The ``source_path`` on the returned Plan is set to
    ``path`` so subsequent error messages can name the file.
    """
    text = path.read_text()
    return parse_plan(text, source_path=path)


def _lock_path(path: Path) -> Path:
    """Return the sidecar lock file path for ``path``.

    Locking the data file directly would race with ``os.replace``
    (the rename atomically swaps a new inode under the existing
    name, so a lock held on the old inode no longer protects the
    new one). A sidecar ``.lock`` file is a stable inode across
    saves and works even when the target does not yet exist.
    """
    return path.with_name(path.name + ".lock")


@contextlib.contextmanager
def _acquire_exclusive_lock(path: Path) -> Iterator[None]:
    """Hold an exclusive advisory ``fcntl.flock`` on ``path``'s sidecar.

    Opens ``<path>.lock`` with ``O_CREAT|O_RDWR``, acquires
    ``LOCK_EX`` (blocking), yields, then releases and closes. Lock
    is released both on normal exit and on exception so a failing
    caller does not leak the lock to subsequent callers.
    """
    lock_path = _lock_path(path)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _save_unlocked(path: Path, plan: Plan) -> None:
    """Atomically write ``plan`` to ``path`` without acquiring the lock.

    Internal helper so :func:`update` can save while still holding
    the lock it already acquired, without double-locking the same
    fd. Public callers go through :func:`save` which wraps this in
    the advisory lock.
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


def save(path: Path, plan: Plan) -> None:
    """Atomically write ``plan`` to ``path`` under an exclusive lock.

    Renders the plan, writes the bytes to a tempfile in the same
    directory, ``fsync``s the file descriptor, then ``os.replace``s the
    tempfile over ``path``. A crash between the write and the rename
    leaves the original file intact; a crash after the rename leaves
    the new file intact. The tempfile is removed on any pre-rename
    failure so failed writes do not litter the directory.

    The whole write is performed under an exclusive advisory lock on
    the sidecar ``<path>.lock`` file, so a concurrent :func:`save` or
    :func:`update` blocks rather than interleaving.
    """
    with _acquire_exclusive_lock(path):
        _save_unlocked(path, plan)


def update(path: Path, operation: Callable[[Plan], Plan]) -> Plan:
    """Safe-mutation entry point: load, lock, re-parse, apply, save.

    The sequence (per design doc Stage 6 spec):

    1. Read the file once unlocked — the caller's baseline view of
       the on-disk content.
    2. Acquire :func:`_acquire_exclusive_lock` on the sidecar lock
       file (blocking).
    3. Re-read the file under the lock and compare bytes to the
       baseline. If different, an external editor wrote to the file
       between step 1 and step 2 (or while we were waiting for the
       lock), and we raise :class:`ConcurrentUpdateError` so the
       caller can decide what to do rather than silently clobbering.
    4. Parse the current bytes, apply ``operation`` to the resulting
       :class:`Plan`, and write the returned Plan atomically via
       :func:`_save_unlocked` while still holding the lock.
    5. Release the lock and return the new Plan.

    ``operation`` is invoked with the freshly parsed Plan; it must
    return a Plan (typically a ``dataclasses.replace`` of the input).
    Mutating the input Plan in place has no effect — the typed model
    is frozen — so callers always produce a new value.
    """
    pre_text = path.read_text()
    with _acquire_exclusive_lock(path):
        post_text = path.read_text()
        if pre_text != post_text:
            raise ConcurrentUpdateError(path)
        plan = parse_plan(post_text, source_path=path)
        new_plan = operation(plan)
        _save_unlocked(path, new_plan)
        return new_plan
