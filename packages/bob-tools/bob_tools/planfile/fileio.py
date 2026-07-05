"""File operations: load, save, and update typed Plan objects on disk.

This module is the I/O boundary for the planfile library. Pure parsing
and rendering live in :mod:`bob_tools.planfile.parser` and
:mod:`bob_tools.planfile.renderer`; everything that touches the
filesystem lives here so the rest of the library can stay
side-effect-free and easy to test.

``save`` writes atomically: the new content is written to a sibling
tempfile, ``fsync``'d, renamed over the destination, and the containing
directory is ``fsync``'d so both the file contents and the rename are
durable — a crash between write and rename never leaves a half-written
PLAN.md, and a crash right after the rename never loses it. ``save``
also holds an advisory exclusive ``fcntl.flock`` on a sidecar lock
file for the duration of the write, so a concurrent ``save`` or
``update`` cannot interleave. ``update`` is the safe-mutation entry
point for tools that race with humans: it loads, locks, re-reads to
detect concurrent external modification (raising
:class:`ConcurrentUpdateError` if the bytes on disk changed between
the unlocked load and the lock acquisition), applies the caller's
``operation``, saves while holding the same lock, and returns the new
Plan.

Per v4 Decision 4, both ``save`` and ``update`` take a keyword-only
``validation`` argument whose literal value is either ``"canonical"``
(the default) or ``"unchecked"``. ``canonical`` is the storage-integrity
gate: it runs ``validate_plan(plan, constructed=True,
require_acceptance=False)`` followed by
:func:`bob_tools.planfile.operations.assert_mcloop_canonical`, and
writes the exact rendered text the latter approved. So ``canonical``
guarantees the mcloop canonical-input contract (R1/R2 + semantic
round-trip), no duplicate task ids, AND the constructed-mode STRUCTURAL
invariants (``magic_version == 1``, contiguous ordinals, phase
keyword/phase_id, no duplicate phase ids, task id presence/format, no
``trailing_lines``, scalar field-stability INCLUDING the embedded-newline
preamble check, per-task field-stability) — a plan violating any of
those cannot reach disk through the default path. ``canonical`` does
NOT enforce declared-acceptance completeness: acceptance is a proof
contract enforced at the AUTHORING layer (duplo authoring paths /
:func:`add_phase_task` / mcloop enforce, all at the default
``require_acceptance=True``), not at the save gate. This split exists
for the legacy-migration window, where a pre-acceptance PLAN.md must be
repairable and re-saveable structurally without first backfilling
acceptance onto every legacy leaf task.
``unchecked`` falls back to a plain :func:`render_plan` and is
intended only for low-level tests of atomic-write, lock, and crash
behavior — call sites outside ``bob_tools.planfile.tests`` are gated
by CI grep (see PLAN.md T-000183). The in-lock save inside
:func:`update` honors the same mode; the underlying atomic-write
helper takes already-validated text and is therefore not a validation
bypass.

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
import dataclasses
import errno
import fcntl
import os
import re
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Literal

from bob_tools.planfile.model import Plan, Task
from bob_tools.planfile.operations import (
    _iter_plan_tasks,
    assert_mcloop_canonical,
    validate_plan,
)
from bob_tools.planfile.parser import parse_plan
from bob_tools.planfile.renderer import render_plan

ValidationMode = Literal["canonical", "unchecked"]


_FULLY_QUALIFIED_TASK_ID_RE = re.compile(r"^T-[A-Za-z]{2}-\d{6}$")


class TaskNotFoundError(LookupError):
    """Raised by :func:`resolve_global` when no PLAN.md carries the id.

    Carries the searched id and the workspace root so callers writing
    cross-file lookups can surface the same context to the user without
    re-stringifying the inputs.
    """

    def __init__(self, task_id: str, root: Path) -> None:
        super().__init__(f"task {task_id!r} not found in any PLAN.md under {root}")
        self.task_id = task_id
        self.root = root


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

    The read is pinned to UTF-8 so a non-UTF-8 platform locale cannot
    misdecode a plan carrying non-ASCII text; every writer in this
    module (and the backfill writer) emits UTF-8, so the read must
    match.
    """
    text = path.read_text(encoding="utf-8")
    return parse_plan(text, source_path=path)


def resolve_global(task_id: str, root: Path) -> tuple[Path, Task]:
    """Resolve a fully-qualified namespaced task id across the workspace.

    Walks every ``PLAN.md`` under ``root`` (recursively, sorted by path
    for determinism), parses each, and returns ``(file, task)`` for the
    first task whose ``task_id`` equals ``task_id``. Raises
    :class:`TaskNotFoundError` when no PLAN.md carries the id.

    ``task_id`` must be in the fully-qualified namespaced form
    ``T-XX-NNNNNN`` (T-000003 grammar). Legacy unprefixed ``T-NNNNNN``
    ids are not addressable through this resolver — they are ambiguous
    across files by construction, which is precisely the reason the
    namespace prefix was added. :class:`ValueError` is raised when the
    input does not match the canonical form.

    Parse errors from any walked PLAN.md propagate unchanged so a
    malformed file is visible to the caller rather than silently
    skipped; a caller that wants tolerant scanning can ``except
    PlanSyntaxError`` around the call.
    """
    if _FULLY_QUALIFIED_TASK_ID_RE.fullmatch(task_id) is None:
        raise ValueError(
            f"task_id must be fully qualified (T-XX-NNNNNN), got {task_id!r}"
        )
    for plan_path in sorted(root.rglob("PLAN.md")):
        plan = load(plan_path)
        for task in _iter_plan_tasks(plan):
            if task.task_id == task_id:
                return plan_path, task
    raise TaskNotFoundError(task_id, root)


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


def _clear_task_trailing_lines(task: Task) -> Task:
    """Return ``task`` (and its subtree) with ``trailing_lines`` cleared.

    Trailing lines are lossless source-position trivia the parser
    captures verbatim; the semantic normalizers used by both the
    field-stability harness and :func:`assert_mcloop_canonical` already
    clear them before comparing, so removing them changes nothing the
    structural validator legitimately inspects. Used only to build the
    validation-only copy in :func:`_render_for_validation` when
    ``allow_trailing_lines`` is set.
    """
    return dataclasses.replace(
        task,
        trailing_lines=(),
        children=tuple(_clear_task_trailing_lines(child) for child in task.children),
    )


def _clear_trailing_lines(plan: Plan) -> Plan:
    """Return a copy of ``plan`` with every task's ``trailing_lines`` cleared.

    Recurses through phase tasks, subsection tasks, and bug-section tasks
    (and each task's child subtree). The returned plan is used *only* to
    satisfy the constructed-mode structural gate; the real bytes are still
    rendered from the untouched ``plan`` so captured trailing lines survive
    to disk byte-for-byte.
    """
    phases = tuple(
        dataclasses.replace(
            phase,
            tasks=tuple(_clear_task_trailing_lines(task) for task in phase.tasks),
            subsections=tuple(
                dataclasses.replace(
                    sub,
                    tasks=tuple(_clear_task_trailing_lines(task) for task in sub.tasks),
                )
                for sub in phase.subsections
            ),
        )
        for phase in plan.phases
    )
    bugs = plan.bugs
    if bugs is not None:
        bugs = dataclasses.replace(
            bugs,
            tasks=tuple(_clear_task_trailing_lines(task) for task in bugs.tasks),
        )
    return dataclasses.replace(plan, phases=phases, bugs=bugs)


def _render_for_validation(
    plan: Plan,
    validation: ValidationMode,
    path: Path | None,
    *,
    magic: bool = True,
    allow_trailing_lines: bool = False,
) -> str:
    """Return the bytes to commit for ``plan`` under the given ``validation``.

    Per v4 Decision 4: ``canonical`` runs the constructed-mode
    STRUCTURAL invariants via ``validate_plan(constructed=True,
    require_acceptance=False)`` (acceptance deferred to the authoring
    layer), then delegates to :func:`assert_mcloop_canonical`, which
    validates the plan against the mcloop canonical-input contract and
    returns the exact rendered text it inspected — so the bytes on disk
    equal the bytes the validator approved. ``unchecked`` skips
    validation and returns a plain :func:`render_plan` of the input.
    ``path`` is forwarded to the validator so a re-parse syntax error
    names the file.

    ``magic`` mirrors the ``save``/``update`` flag: when ``False`` the
    plan's magic line has already been dropped (``magic_version`` cleared
    to ``None``), so the constructed-mode structural gate is told to
    expect a cleared magic line via ``allow_cleared_magic=True``. Without
    this the ``magic_version != 1`` invariant would always fire on a
    magic-less plan and :func:`assert_mcloop_canonical` — documented to
    accept a cleared magic line — would never be reached, making the
    loose-queue path unusable.

    ``allow_trailing_lines`` exempts the constructed-mode "no
    ``trailing_lines``" invariant, which exists to catch construction-API
    tasks that smuggle in raw source lines. A plan parsed from a file
    legitimately carries lossless trailing lines (a completed task
    followed by a fenced output block, inter-section spacing, ...), so
    :func:`bob_tools.planfile.cli.cmd_fmt` — whose whole job is to
    canonicalize a file in place — sets this. The exemption is achieved
    by running the structural validator against a trailing-lines-cleared
    copy while :func:`assert_mcloop_canonical` still renders the untouched
    ``plan``, so the captured lines round-trip to disk byte-for-byte.
    Because both semantic normalizers already clear ``trailing_lines``
    before comparing, the cleared copy validates identically to the
    original in every other respect.

    Centralizing the choice here keeps :func:`save` and the in-lock
    save inside :func:`update` honoring the same mode without
    duplicating the branch.
    """
    if validation == "canonical":
        # Storage-integrity gate: enforce the constructed-mode STRUCTURAL
        # invariants (magic_version, contiguous ordinals, phase keyword/
        # id, duplicate phase ids, task id presence/format, trailing_lines,
        # scalar field-stability INCLUDING the embedded-newline preamble
        # check, per-task field-stability) but NOT acceptance-completeness.
        # Acceptance is a proof contract enforced at the authoring layer
        # (duplo authoring / add_phase_task / mcloop enforce) during the
        # legacy-migration window, not at the save gate.
        plan_for_structural = (
            _clear_trailing_lines(plan) if allow_trailing_lines else plan
        )
        validate_plan(
            plan_for_structural,
            constructed=True,
            require_acceptance=False,
            allow_cleared_magic=not magic,
        )
        return assert_mcloop_canonical(plan, source_path=path)
    if validation == "unchecked":
        return render_plan(plan)
    raise ValueError(
        f"validation must be 'canonical' or 'unchecked', got {validation!r}"
    )


def _atomic_write_text(path: Path, text: str) -> None:
    """Atomically write ``text`` to ``path`` without acquiring the lock.

    Write-only helper: takes already-rendered (and, for canonical
    callers, already-validated) text and commits it to disk. Replaces
    the previous ``_save_unlocked`` helper, which accepted a Plan and
    therefore could be (mis)used to bypass validation. By splitting
    the validate-and-render step out, the atomic-write path no longer
    knows about Plans at all, so there is no in-process path that
    writes a Plan without going through :func:`_render_for_validation`.

    Writes to a sibling tempfile (UTF-8, pinned so the on-disk encoding
    never depends on the platform locale), ``fsync``s the descriptor,
    ``os.replace``s the tempfile over ``path``, then ``fsync``s the
    containing directory so the rename itself is durable — without the
    directory fsync a crash right after the rename can lose the update
    even though the file contents were fsync'd, defeating the
    crash-safety claim. The tempfile is unlinked on any pre-rename
    failure so failed writes do not litter the directory. Used by
    :func:`save` (under the lock it acquires) and by :func:`update`
    (under the lock it already holds).
    """
    directory = path.parent if path.parent != Path("") else Path(".")
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(directory),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
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
    _fsync_directory(directory)


def _fsync_directory(directory: Path) -> None:
    """``fsync`` ``directory`` so a preceding ``os.replace`` is durable.

    A file rename is a directory metadata change; fsyncing the file
    descriptor makes the *contents* durable but not the directory entry
    that points at the new inode. Without this a crash immediately after
    ``os.replace`` can leave the directory still referencing the old
    inode, silently losing the just-committed update.

    Some filesystems reject ``fsync`` on a directory descriptor with
    ``EINVAL``; that is a platform limitation, not a durability failure
    of this write, so it is swallowed. Any other error propagates.
    """
    dir_fd = os.open(str(directory), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    except OSError as exc:
        if exc.errno != errno.EINVAL:
            raise
    finally:
        os.close(dir_fd)


def save(
    path: Path,
    plan: Plan,
    *,
    validation: ValidationMode = "canonical",
    magic: bool = True,
    allow_trailing_lines: bool = False,
) -> None:
    """Atomically write ``plan`` to ``path`` under an exclusive lock.

    Per v4 Decision 4, ``validation`` selects how the bytes-to-commit
    are produced:

    * ``"canonical"`` (default) — run the constructed-mode structural
      invariants (``validate_plan(constructed=True,
      require_acceptance=False)``) then
      :func:`assert_mcloop_canonical` against ``plan`` and write the
      exact rendered text the validator returned. A plan that fails the
      structural invariants or the mcloop canonical-input contract
      raises :class:`PlanValidationError` and never reaches disk.
      Declared-acceptance completeness is NOT checked here; it is an
      authoring-layer contract (see the module docstring).
    * ``"unchecked"`` — render with :func:`render_plan` and write the
      result without validation. Reserved for low-level tests of
      atomic-write, lock, and crash behavior; non-test call sites are
      gated by CI grep.

    Validation runs outside the lock (it is pure on ``plan``) so a
    rejected plan never causes lock contention. The atomic write then
    happens under an exclusive advisory lock on the sidecar
    ``<path>.lock`` file: bytes are written to a sibling tempfile,
    ``fsync``'d, and ``os.replace``'d over ``path``. A crash between
    the write and the rename leaves the original file intact; a crash
    after the rename leaves the new file intact. The tempfile is
    removed on any pre-rename failure so failed writes do not litter
    the directory.

    ``magic`` (default ``True``) keeps the canonical magic line. Pass
    ``False`` for a loose queue (mcloop's BUGS.md) so the magic line is
    dropped before render — ``magic_version`` is cleared, which
    ``render_plan`` and ``assert_mcloop_canonical`` both accept.

    ``allow_trailing_lines`` (default ``False``) exempts the
    constructed-mode "no ``trailing_lines``" invariant so a plan parsed
    from a file — which legitimately carries the parser's lossless
    trailing-line capture — can be re-saved through the canonical gate.
    The trailing lines are preserved in the written bytes; only the
    structural validator sees a trailing-lines-cleared copy. Used by
    ``fmt``, whose job is to canonicalize an on-disk file in place.
    """
    if not magic:
        plan = dataclasses.replace(plan, magic_version=None)
    text = _render_for_validation(
        plan, validation, path, magic=magic, allow_trailing_lines=allow_trailing_lines
    )
    with _acquire_exclusive_lock(path):
        _atomic_write_text(path, text)


def update(
    path: Path,
    operation: Callable[[Plan], Plan],
    *,
    validation: ValidationMode = "canonical",
    magic: bool = True,
) -> Plan:
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
       :class:`Plan`, render the returned Plan via
       :func:`_render_for_validation` under the same ``validation``
       mode as :func:`save`, and atomically commit those bytes via
       :func:`_atomic_write_text` while still holding the lock.
    5. Release the lock and return the new Plan.

    ``operation`` is invoked with the freshly parsed Plan; it must
    return a Plan (typically a ``dataclasses.replace`` of the input).
    Mutating the input Plan in place has no effect — the typed model
    is frozen — so callers always produce a new value.

    ``validation`` defaults to ``"canonical"`` per v4 Decision 4 and
    behaves identically to :func:`save`'s parameter; the in-lock save
    honors the same mode so a non-canonical mutation cannot reach
    disk through the default path. Validation runs inside the lock
    because the input plan is the re-parsed on-disk state at that
    moment.

    ``magic`` (default ``True``) keeps today's behavior: the in-lock
    re-parse forces strict on a magic-lined file and the render re-emits
    the magic line. Pass ``False`` for a loose queue (mcloop's BUGS.md):
    the re-parse does not force strict (id-less entries survive) and the
    magic line is dropped from the written bytes.
    """
    pre_text = path.read_text(encoding="utf-8")
    with _acquire_exclusive_lock(path):
        post_text = path.read_text(encoding="utf-8")
        if pre_text != post_text:
            raise ConcurrentUpdateError(path)
        plan = parse_plan(post_text, source_path=path, force_strict_from_magic=magic)
        new_plan = operation(plan)
        if not magic:
            new_plan = dataclasses.replace(new_plan, magic_version=None)
        text = _render_for_validation(new_plan, validation, path, magic=magic)
        _atomic_write_text(path, text)
        return new_plan
