"""Stage 6 acceptance tests for bob_tools.planfile.fileio.

The PLAN.md Stage 6 spec lists three falsifying tests for the
``load``/``save``/``update`` surface:

* atomic write must not leave half-written files on a simulated crash;
* :func:`update`'s advisory lock must serialize two concurrent calls;
* :func:`update` must detect a mid-flight external edit and raise.

All three exercise :func:`bob_tools.planfile.fileio.update`, which was
a :class:`NotImplementedError` stub before the Stage 6 implementation
landed; each of the tests below therefore fails against the prior
stub (either by hitting the raise or, for the crash-safety test, by
covering a code path the stub never reached).
"""

from __future__ import annotations

import contextlib
import dataclasses
import errno
import os
import stat
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from bob_tools.planfile import (
    ConcurrentUpdateError,
    Plan,
    PlanSyntaxError,
    PlanValidationError,
    fileio,
    load,
    parse_plan,
    save,
    update,
)

# Legacy-but-mcloop-canonical fixture: missing the v1 magic preamble and
# the <!-- phase_id: ... --> comment, but every incomplete checkbox sits
# under a Stage header and every parsed task carries a T-NNNNNN id. It
# satisfies assert_mcloop_canonical after the Contract 5 amendment that
# split mcloop's R1/R2 canonical-input contract from constructed=True
# construction-API strictness.
_MINIMAL_PLAN = "# Stage 6 fixture\n\n## Stage 1: Smoke\n\n- [ ] T-000001: only task\n"

# Truly non-canonical for mcloop's R2 predicate: the parsed task lacks a
# stable T-NNNNNN id. Default canonical save/update must reject this.
_IDLESS_PLAN = "# Stage 6 fixture\n\n## Stage 1: Smoke\n\n- [ ] only task\n"

# Canonical fixture: contains the v1 magic preamble, a phase_id
# comment, and a T-NNNNNN id, so it passes
# assert_mcloop_canonical and validate_plan(constructed=True). Used by
# ordinary strict save/update tests that need construction-API shape.
#
# The H1 deliberately avoids the words "Stage"/"Phase" followed by a
# digit; ``parser._STAGE_RE`` accepts ``#+`` so an H1 like
# "# Stage 6 fixture" is misparsed as a phase heading rather than the
# project title, which would fail constructed-mode validation.
_CANONICAL_PLAN = (
    "<!-- bob-plan-format: 1 -->\n"
    "\n"
    "# Canonical fileio fixture\n"
    "\n"
    "## Stage 1: Smoke\n"
    "<!-- phase_id: phase_001 -->\n"
    "\n"
    "- [ ] T-000001: only task [accept: pytest]\n"
)


def _write(path: Path, text: str) -> None:
    path.write_text(text)


def _retitle(new_title: str):  # type: ignore[no-untyped-def]
    """Return an ``operation`` that swaps ``Plan.project_title``."""

    def _op(plan: Plan) -> Plan:
        return dataclasses.replace(plan, project_title=new_title)

    return _op


def test_save_crash_between_write_and_rename_preserves_original(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failure in ``os.replace`` leaves the original file intact
    and removes the half-written tempfile.

    Why this falsifies the prior stub: the prior stub never wrote
    anything because :func:`update` raised on entry; no atomic-write
    behavior was ever exercised. This test pins the contract by
    monkeypatching ``os.replace`` to raise so the rename step fails
    after a successful ``fsync``, then asserts (1) the file still
    holds its pre-save bytes, and (2) no leftover ``PLAN.md.*.tmp``
    sibling remains.
    """
    path = tmp_path / "PLAN.md"
    _write(path, _MINIMAL_PLAN)
    original_bytes = path.read_bytes()

    plan = load(path)
    new_plan = _retitle("CHANGED ON RENAME CRASH")(plan)

    def _boom(src: str, dst: str) -> None:
        raise OSError(errno.EIO, "simulated rename crash", dst)

    monkeypatch.setattr("os.replace", _boom)

    # validation="unchecked": the assertion under test is atomic-write
    # crash behavior, not canonical-input enforcement.
    with pytest.raises(OSError, match="simulated rename crash"):
        save(path, new_plan, validation="unchecked")

    assert path.read_bytes() == original_bytes, (
        "original PLAN.md must be untouched on a write/rename crash"
    )
    leftovers = [
        p
        for p in tmp_path.iterdir()
        if p.name.startswith("PLAN.md.") and p.name.endswith(".tmp")
    ]
    assert leftovers == [], (
        f"tempfile must be unlinked after rename failure; found {leftovers}"
    )


def test_update_lock_serializes_concurrent_calls(tmp_path: Path) -> None:
    """Two concurrent :func:`update` calls on the same path serialize.

    Exactly one of them wins the race: it acquires the lock first,
    re-reads, sees the same bytes it loaded, applies its operation,
    and commits its render to disk. The other thread loaded the
    initial bytes too but blocks on the lock; when it finally
    acquires, it re-reads the (now post-winner) bytes, sees a
    difference from its pre-lock load, and raises
    :class:`ConcurrentUpdateError`. The combination of advisory
    locking and bytes-level re-read detection is what guarantees the
    last-writer-wins race is impossible: either both writers commit
    serially or one of them is told to retry.

    Why this falsifies the prior stub: :func:`update` raised
    ``NotImplementedError`` immediately, so neither lock acquisition
    nor concurrent-edit detection existed. Both threads here would
    instead raise ``NotImplementedError`` before any locking, and
    the post-condition assertion (file holds a winner's title) would
    not hold.
    """
    path = tmp_path / "PLAN.md"
    _write(path, _MINIMAL_PLAN)

    # ``pre_read_done`` opens the gate only after both threads have
    # finished update()'s unlocked baseline read. Without this, a
    # tight winner can complete its entire save before the loser
    # even starts, and both threads end up reading the same on-disk
    # content (no race to detect). The barrier forces an interleave
    # the lock must serialize.
    pre_read_done = threading.Barrier(2)
    proceed = threading.Event()
    errors: list[Exception] = []
    errors_lock = threading.Lock()

    def make_op(label: str):  # type: ignore[no-untyped-def]
        def op(plan: Plan) -> Plan:
            # Hold the lock long enough for the loser to be
            # blocked on lock acquisition, not finishing before
            # the loser even starts.
            time.sleep(0.2)
            return dataclasses.replace(plan, project_title=label)

        return op

    real_acquire = fileio._acquire_exclusive_lock

    def patched_acquire(path_arg: Path):  # type: ignore[no-untyped-def]
        # Wait for both threads to have completed update()'s
        # pre-lock read before either is permitted to acquire the
        # lock. This is what creates the race the lock must resolve.
        pre_read_done.wait()
        proceed.wait()
        return real_acquire(path_arg)

    fileio._acquire_exclusive_lock = patched_acquire  # type: ignore[assignment]

    def runner(label: str) -> None:
        try:
            # validation="unchecked": this test pins lock
            # serialization, not canonical enforcement.
            update(path, make_op(label), validation="unchecked")
        except ConcurrentUpdateError as exc:
            with errors_lock:
                errors.append(exc)

    try:
        t1 = threading.Thread(target=runner, args=("Alpha",))
        t2 = threading.Thread(target=runner, args=("Beta",))
        t1.start()
        t2.start()
        # Both threads should now be parked in patched_acquire
        # after completing their pre_text read. Release them.
        proceed.set()
        t1.join(timeout=10.0)
        t2.join(timeout=10.0)
        assert not t1.is_alive() and not t2.is_alive(), "thread hang"
    finally:
        fileio._acquire_exclusive_lock = real_acquire

    assert len(errors) == 1, (
        f"expected exactly one ConcurrentUpdateError (race loser), got "
        f"{len(errors)}: {errors}"
    )
    final = parse_plan(path.read_text())
    assert final.project_title in ("Alpha", "Beta"), (
        f"final title must be one of the racers, got {final.project_title!r}"
    )


def test_update_detects_mid_flight_external_edit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An external write between the unlocked load and the locked
    re-read causes :func:`update` to raise.

    The race window the spec calls out: a tool reads the file
    (without holding the lock), then attempts to update; before
    the tool's lock acquisition completes, a human or another
    non-lock-respecting writer modifies the file. The lock cannot
    prevent that (it is advisory), so :func:`update` must detect
    it via bytes-level comparison and refuse to overwrite.

    The test injects the external edit inside a monkeypatched lock
    helper: just before yielding control back to ``update``, the
    helper rewrites the file with different content. From
    ``update``'s perspective this is indistinguishable from a real
    external editor that wrote after the unlocked load but before
    (or while) the lock was being acquired.

    Why this falsifies the prior stub: ``update`` never reached the
    re-read or comparison; it raised ``NotImplementedError`` on the
    very first line. The mid-flight detection branch had no
    coverage and no behavioral guarantee.
    """
    path = tmp_path / "PLAN.md"
    _write(path, _MINIMAL_PLAN)
    modified_text = _MINIMAL_PLAN + "- [ ] T-000002: injected externally\n"

    real_acquire = fileio._acquire_exclusive_lock

    @contextlib.contextmanager
    def acquire_after_external_edit(p: Path) -> Iterator[None]:
        # Simulate a non-lock-respecting writer that lands between
        # update()'s unlocked read and its lock acquisition.
        _write(p, modified_text)
        with real_acquire(p):
            yield

    monkeypatch.setattr(fileio, "_acquire_exclusive_lock", acquire_after_external_edit)

    # validation="unchecked": this test pins the mid-flight external-
    # edit detection, not canonical enforcement. The injected external
    # content and the _MINIMAL_PLAN baseline are both deliberately
    # non-canonical; the default canonical mode would not change the
    # outcome here (the ConcurrentUpdateError is raised before the
    # render/validate step), but staying on unchecked keeps the focus
    # on the race-detection contract under test.
    with pytest.raises(ConcurrentUpdateError) as exc_info:
        update(path, _retitle("would clobber"), validation="unchecked")
    assert exc_info.value.path == path

    # Sanity: the externally-written bytes are still on disk; the
    # would-be update did not save its rendering over them.
    assert path.read_text() == modified_text, (
        "ConcurrentUpdateError must abort before save; on-disk bytes "
        "must equal the externally-written content"
    )


def test_update_happy_path_returns_new_plan_and_persists(tmp_path: Path) -> None:
    """Sanity post-condition for the no-race, canonical-mode case.

    With no concurrent writer, :func:`update` returns the
    ``operation``'s output Plan and writes it to disk such that a
    subsequent :func:`load` recovers the same content. Uses the
    default ``validation="canonical"`` against ``_CANONICAL_PLAN``
    (per T-000181: ordinary update fixtures must be canonical) so
    the happy path exercises the validate-and-write branch rather
    than the unchecked fallback. Guards against a regression where
    the locked branch silently drops the save, fails to return the
    new Plan to the caller, or writes bytes other than the ones the
    validator approved.
    """
    path = tmp_path / "PLAN.md"
    _write(path, _CANONICAL_PLAN)

    returned = update(path, _retitle("Renamed Title"))
    assert returned.project_title == "Renamed Title"

    reloaded = load(path)
    assert reloaded.project_title == "Renamed Title"


def test_save_holds_advisory_lock_while_writing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """:func:`save` acquires :func:`_acquire_exclusive_lock` for the
    duration of its atomic write.

    Verifies the spec's "save also locks" rule by intercepting the
    helper and counting acquisitions per save. Independent of
    :func:`update`'s own locking so a future refactor that separates
    the two paths still has to maintain the save-locks-on-its-own
    guarantee. Why this falsifies the prior stub: the prior save did
    not lock at all, so an instrumented helper would not be called.
    """
    path = tmp_path / "PLAN.md"
    _write(path, _MINIMAL_PLAN)

    real_acquire = fileio._acquire_exclusive_lock
    calls: list[Path] = []

    @contextlib.contextmanager
    def counting_acquire(p: Path) -> Iterator[None]:
        calls.append(p)
        with real_acquire(p):
            yield

    monkeypatch.setattr(fileio, "_acquire_exclusive_lock", counting_acquire)

    plan = load(path)
    # validation="unchecked": this test pins the save-locks-on-its-
    # own contract. It should observe exactly one lock acquisition
    # without depending on canonical validation behavior.
    save(path, plan, validation="unchecked")

    assert calls == [path], (
        f"save() must acquire the exclusive lock exactly once for the "
        f"target path; got {calls}"
    )


# --- Stage 16 gate (T-000182) -------------------------------------------------
#
# These tests pin the four properties the Stage 16 verification gate calls
# out: save's default raises on a non-canonical plan; unchecked still
# writes; update honors the same mode; and there is no _save_unlocked
# Plan-taking helper left in fileio that could bypass validation. They
# are intentionally minimal — the substantive canonical/atomic-write
# contracts are pinned elsewhere — but they make the gate itself a unit-
# tested invariant so a future change that re-introduces a bypass (or
# silently flips the default to unchecked) fails here rather than only
# at the next manual gate verification.


def test_save_default_canonical_rejects_non_canonical_plan(tmp_path: Path) -> None:
    """save() with the default validation refuses a non-canonical plan.

    Loading ``_IDLESS_PLAN`` yields a Plan that parses fine but fails
    mcloop's R2 canonical-input predicate. The default canonical mode
    must raise :class:`PlanValidationError` before the atomic-write path
    is reached, so the on-disk bytes remain the original fixture.
    """
    path = tmp_path / "PLAN.md"
    _write(path, _IDLESS_PLAN)
    original_bytes = path.read_bytes()
    plan = load(path)

    with pytest.raises(PlanValidationError):
        save(path, plan)

    assert path.read_bytes() == original_bytes, (
        "rejected save must not modify the file on disk"
    )


def test_save_default_canonical_rejects_non_constructed_plan(
    tmp_path: Path,
) -> None:
    """The default canonical save gate is the storage-integrity gate.

    It enforces the constructed-mode STRUCTURAL invariants (here a
    missing ``magic_version``), so a non-constructed legacy plan is
    rejected and never reaches disk through the default path. Migrating
    a legacy plan to constructed form is the runtime preflight's job
    (``preflight_runtime_plan``), not the save gate's. Acceptance
    completeness, by contrast, is deferred to the authoring layer and
    is NOT checked here.
    """
    path = tmp_path / "PLAN.md"
    _write(path, _MINIMAL_PLAN)
    plan = load(path)
    assert plan.magic_version is None
    new_plan = dataclasses.replace(plan, project_title="Legacy Canonical")

    with pytest.raises(PlanValidationError):
        save(path, new_plan)


def test_save_unchecked_writes_non_canonical_plan(tmp_path: Path) -> None:
    """save(..., validation='unchecked') writes a non-canonical plan.

    Companion to the canonical-rejects test: the opt-out really does
    skip validation. A round-trip through load -> save(unchecked) -> load
    with the non-canonical ``_IDLESS_PLAN`` fixture is observable
    on disk. Pins that unchecked is a real escape hatch (so the low-
    level lock/crash tests above are not silently asserting against
    canonical-mode behavior) and that it does not require any other
    keyword to opt in.
    """
    path = tmp_path / "PLAN.md"
    _write(path, _IDLESS_PLAN)
    plan = load(path)
    new_plan = dataclasses.replace(plan, project_title="Unchecked Write")

    save(path, new_plan, validation="unchecked")

    reloaded = load(path)
    assert reloaded.project_title == "Unchecked Write"


def test_update_default_canonical_rejects_non_canonical_result(tmp_path: Path) -> None:
    """update() with the default validation refuses a non-canonical result.

    The on-disk file parses but fails mcloop's R2 predicate. The default
    canonical mode must raise :class:`PlanValidationError` in the in-lock
    save step and must not overwrite the existing bytes.
    """
    path = tmp_path / "PLAN.md"
    _write(path, _IDLESS_PLAN)
    original_bytes = path.read_bytes()

    with pytest.raises(PlanValidationError):
        update(path, _retitle("default canonical update"))

    assert path.read_bytes() == original_bytes, (
        "rejected update must not modify the file on disk"
    )


def test_update_unchecked_writes_non_canonical_result(tmp_path: Path) -> None:
    """update(..., validation='unchecked') persists a non-canonical result.

    Mirror of the save-unchecked test for the update surface: the
    explicit opt-out commits the operation's output even when the plan
    would not satisfy ``assert_mcloop_canonical``. Pins that update's
    ``validation`` kwarg is honored (not silently overridden by the
    default) and that the unchecked path round-trips through the
    in-lock save.
    """
    path = tmp_path / "PLAN.md"
    _write(path, _IDLESS_PLAN)

    returned = update(path, _retitle("Unchecked Update"), validation="unchecked")
    assert returned.project_title == "Unchecked Update"

    reloaded = load(path)
    assert reloaded.project_title == "Unchecked Update"


def test_fileio_module_has_no_save_unlocked_bypass() -> None:
    """The pre-Stage-16 ``_save_unlocked`` Plan-taking helper is gone.

    The atomic-write helper that exists today (``_atomic_write_text``)
    takes already-rendered text, not a Plan, so it cannot be misused
    as a validation bypass. A future refactor that re-introduces a
    helper named ``_save_unlocked`` (or one that takes a Plan and
    writes without going through ``_render_for_validation``) would
    weaken the gate; this test fails loudly if either happens.
    """
    assert not hasattr(fileio, "_save_unlocked"), (
        "fileio must not expose a Plan-taking _save_unlocked helper; "
        "atomic-write should run on already-validated text only"
    )


# --- magic= opt-out (loose-queue writes drop the magic line) ----------------

# A magic-lined, id-less loose queue (mcloop's BUGS.md after the bug-filer
# appends an entry without a T-id).
_MAGIC_IDLESS_QUEUE = "<!-- bob-plan-format: 1 -->\n\n## Bugs\n- [ ] loose bug, no id\n"


def test_save_magic_false_drops_magic_line(tmp_path: Path) -> None:
    """save(magic=False) clears the magic line for a loose queue.

    Rendered for a loose queue (validation='unchecked'): the
    ``<!-- bob-plan-format: 1 -->`` preamble must not appear.
    """
    path = tmp_path / "BUGS.md"
    plan = parse_plan(_CANONICAL_PLAN)
    assert plan.magic_version == 1
    save(path, plan, validation="unchecked", magic=False)
    assert "<!-- bob-plan-format:" not in path.read_text()


def test_save_canonical_magic_false_succeeds(tmp_path: Path) -> None:
    """T-000004 regression: a canonical save of a magic-less plan succeeds.

    ``save(validation='canonical', magic=False)`` clears ``magic_version``
    to ``None`` for a loose queue. Before the fix, the constructed-mode
    gate inside ``_render_for_validation`` unconditionally appended a
    ``magic_version must be 1`` error, so this raised
    :class:`PlanValidationError` and ``assert_mcloop_canonical`` — which
    is documented to accept a cleared magic line — was never reached. The
    documented loose-queue path is now usable without falling back to
    ``validation='unchecked'``.
    """
    path = tmp_path / "BUGS.md"
    plan = parse_plan(_CANONICAL_PLAN)
    assert plan.magic_version == 1
    save(path, plan, validation="canonical", magic=False)
    text = path.read_text()
    assert "<!-- bob-plan-format:" not in text
    assert "T-000001: only task" in text


def test_update_canonical_magic_false_succeeds(tmp_path: Path) -> None:
    """T-000004 regression (update path): canonical + magic=False commits.

    The in-lock save inside :func:`update` routes through the same
    ``_render_for_validation`` gate as :func:`save`, so it was equally
    broken. This exercises the mutation entry point end-to-end on a
    magic-less loose queue under the default canonical mode.
    """
    path = tmp_path / "BUGS.md"
    path.write_text(_CANONICAL_PLAN)
    returned = update(
        path, _retitle("Retitled loose queue"), validation="canonical", magic=False
    )
    assert returned.magic_version is None
    text = path.read_text()
    assert "<!-- bob-plan-format:" not in text
    assert "# Retitled loose queue" in text


def test_save_canonical_magic_false_still_rejects_idless(tmp_path: Path) -> None:
    """GUARD: the magic-less canonical save still enforces every OTHER
    constructed-mode invariant. Clearing magic must not relax mcloop's R2
    id-less contract, so an id-less task is still rejected."""
    path = tmp_path / "BUGS.md"
    plan = parse_plan(_IDLESS_PLAN)
    with pytest.raises(PlanValidationError):
        save(path, plan, validation="canonical", magic=False)


# A plan whose completed task tail carries a fenced code block; the
# parser captures the fence lines as that task's ``trailing_lines``.
_TRAILING_LINES_PLAN = (
    "<!-- bob-plan-format: 1 -->\n"
    "\n"
    "# Canonical fileio fixture\n"
    "\n"
    "## Stage 1: Smoke\n"
    "<!-- phase_id: phase_001 -->\n"
    "\n"
    "- [x] T-000001: ran the linter [accept: pytest]\n"
    "  ```\n"
    "  ruff output\n"
    "  ```\n"
    "\n"
    "- [ ] T-000002: only task [accept: pytest]\n"
)


def test_save_canonical_default_rejects_trailing_lines(tmp_path: Path) -> None:
    """T-000010 GUARD: without ``allow_trailing_lines`` the canonical save
    still rejects a task carrying ``trailing_lines``. The construction-API
    invariant that catches raw source lines smuggled into a built task
    must stay in force on the default path."""
    path = tmp_path / "PLAN.md"
    plan = parse_plan(_TRAILING_LINES_PLAN)
    assert plan.phases[0].tasks[0].trailing_lines  # parser captured them
    with pytest.raises(PlanValidationError):
        save(path, plan, validation="canonical")


def test_save_canonical_allow_trailing_lines_preserves_block(tmp_path: Path) -> None:
    """T-000010 regression: ``save(allow_trailing_lines=True)`` exempts the
    trailing-lines invariant and writes the captured fence block verbatim.

    Only the structural validator sees a trailing-lines-cleared copy;
    ``assert_mcloop_canonical`` renders the untouched plan, so the block
    round-trips to disk byte-for-byte."""
    path = tmp_path / "PLAN.md"
    plan = parse_plan(_TRAILING_LINES_PLAN)
    save(path, plan, validation="canonical", allow_trailing_lines=True)
    text = path.read_text()
    assert "  ```\n  ruff output\n  ```\n" in text
    # every other constructed invariant still holds — re-parse is clean
    # and re-saving the same bytes is a no-op.
    reparsed = parse_plan(text)
    save(path, reparsed, validation="canonical", allow_trailing_lines=True)
    assert path.read_text() == text


def test_save_magic_true_keeps_magic_line(tmp_path: Path) -> None:
    """Default magic=True is unchanged: a canonical plan keeps its magic line."""
    path = tmp_path / "PLAN.md"
    plan = parse_plan(_CANONICAL_PLAN)
    save(path, plan)  # default magic=True, default validation=canonical
    assert "<!-- bob-plan-format: 1 -->" in path.read_text()


def test_update_magic_false_tolerates_idless_magic_lined_queue(tmp_path: Path) -> None:
    """update(magic=False) on a magic-lined id-less queue does NOT raise on
    the in-lock pre-parse, applies the op, and rewrites WITHOUT the magic
    line — closing the loop so subsequent strict reads become moot."""
    path = tmp_path / "BUGS.md"
    path.write_text(_MAGIC_IDLESS_QUEUE)
    # Identity op: we only care that the pre-parse + render survive.
    returned = update(path, lambda plan: plan, validation="unchecked", magic=False)
    assert returned is not None
    text = path.read_text()
    assert "<!-- bob-plan-format:" not in text
    assert "loose bug, no id" in text  # the id-less entry is preserved


def test_update_default_magic_true_still_strict_on_idless(tmp_path: Path) -> None:
    """GUARD (PLAN.md unchanged): with default magic=True the in-lock pre-parse
    still force-enables strict on a magic-lined id-less file and raises."""
    path = tmp_path / "PLAN.md"
    path.write_text(_MAGIC_IDLESS_QUEUE)
    with pytest.raises(PlanSyntaxError):
        update(path, lambda plan: plan, validation="unchecked")


# --- T-000007 durability + encoding pins ------------------------------------

# A canonical fixture whose project title carries non-ASCII text, so the
# UTF-8 pin is observable in the bytes on disk and after a re-parse.
_UNICODE_PLAN = (
    "<!-- bob-plan-format: 1 -->\n"
    "\n"
    "# Café résumé fixture ☕\n"
    "\n"
    "## Stage 1: Smoke\n"
    "<!-- phase_id: phase_001 -->\n"
    "\n"
    "- [ ] T-000001: only task [accept: pytest]\n"
)


def test_atomic_write_pins_utf8_regardless_of_locale(tmp_path: Path) -> None:
    """``_atomic_write_text`` always encodes UTF-8, not the platform locale.

    Before the fix the tempfile was opened with ``os.fdopen(fd, "w")``,
    whose encoding follows ``locale.getpreferredencoding``; on a non-UTF-8
    locale a non-ASCII plan would be re-encoded inconsistently (or fail).
    Pinning ``encoding="utf-8"`` makes the on-disk bytes deterministic:
    they equal the UTF-8 encoding of the text, which we can assert
    directly without perturbing the process locale.
    """
    path = tmp_path / "PLAN.md"
    text = "# café ☕ résumé\n"
    fileio._atomic_write_text(path, text)
    assert path.read_bytes() == text.encode("utf-8")


def test_load_reads_utf8_bytes(tmp_path: Path) -> None:
    """``load`` decodes the file as UTF-8 regardless of platform locale.

    Writing raw UTF-8 bytes and loading must recover the non-ASCII
    title; the prior ``path.read_text()`` used the locale encoding and
    would mojibake or raise on a non-UTF-8 locale.
    """
    path = tmp_path / "PLAN.md"
    path.write_bytes(_UNICODE_PLAN.encode("utf-8"))
    plan = load(path)
    assert plan.project_title == "Café résumé fixture ☕"


def test_save_load_round_trips_non_ascii(tmp_path: Path) -> None:
    """A canonical save of a non-ASCII plan round-trips through load."""
    path = tmp_path / "PLAN.md"
    plan = parse_plan(_UNICODE_PLAN)
    save(path, plan)
    assert path.read_bytes() == render_from(plan)
    assert load(path).project_title == "Café résumé fixture ☕"


def render_from(plan: Plan) -> bytes:
    """Helper: canonical render of ``plan`` as UTF-8 bytes for byte-compare."""
    from bob_tools.planfile.operations import assert_mcloop_canonical

    return assert_mcloop_canonical(plan).encode("utf-8")


def test_save_fsyncs_containing_directory_after_rename(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``save`` fsyncs the directory after ``os.replace`` so the rename is durable.

    The prior ``_atomic_write_text`` fsynced only the tempfile, leaving a
    window where a crash right after the rename could drop the update
    despite the crash-safety claim. This test intercepts the directory
    fsync helper and asserts it runs exactly once against the target's
    parent, after the write. It falsifies the prior code, which had no
    such call at all.
    """
    path = tmp_path / "PLAN.md"
    plan = parse_plan(_MINIMAL_PLAN)

    dirs_fsynced: list[Path] = []
    real_fsync_dir = fileio._fsync_directory

    def _spy(directory: Path) -> None:
        dirs_fsynced.append(directory)
        real_fsync_dir(directory)

    monkeypatch.setattr(fileio, "_fsync_directory", _spy)
    save(path, plan, validation="unchecked")

    assert dirs_fsynced == [tmp_path], (
        f"save() must fsync the containing directory exactly once; got {dirs_fsynced}"
    )


def test_atomic_write_fsyncs_a_directory_descriptor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The directory fsync targets an actual directory descriptor.

    Records the fd kinds passed to ``os.fsync`` during a write and
    asserts at least one is a directory — the tempfile fsync covers the
    contents, the directory fsync covers the rename.
    """
    path = tmp_path / "PLAN.md"
    plan = parse_plan(_MINIMAL_PLAN)

    saw_directory_fsync = []
    real_fsync = os.fsync

    def _record(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            saw_directory_fsync.append(fd)
        real_fsync(fd)

    monkeypatch.setattr("os.fsync", _record)
    save(path, plan, validation="unchecked")

    assert saw_directory_fsync, "no directory descriptor was fsync'd after rename"


def test_directory_fsync_swallows_einval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A filesystem that rejects directory fsync with EINVAL is tolerated.

    Some filesystems return ``EINVAL`` for ``fsync`` on a directory
    descriptor; that is a platform limitation, not a durability failure
    of the write, so the save must still succeed and commit the bytes.
    """
    path = tmp_path / "PLAN.md"
    plan = parse_plan(_MINIMAL_PLAN)

    real_fsync = os.fsync

    def _einval_on_dirs(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(errno.EINVAL, "directory fsync unsupported")
        real_fsync(fd)

    monkeypatch.setattr("os.fsync", _einval_on_dirs)
    save(path, plan, validation="unchecked")

    assert "T-000001: only task" in path.read_text(encoding="utf-8")


def test_directory_fsync_propagates_non_einval_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A non-EINVAL directory fsync error propagates rather than being hidden.

    An ``EIO`` on the directory fsync signals real trouble making the
    rename durable, so it must surface to the caller instead of being
    swallowed like the benign EINVAL case.
    """
    path = tmp_path / "PLAN.md"
    plan = parse_plan(_MINIMAL_PLAN)

    real_fsync = os.fsync

    def _eio_on_dirs(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(errno.EIO, "directory fsync failed")
        real_fsync(fd)

    monkeypatch.setattr("os.fsync", _eio_on_dirs)
    with pytest.raises(OSError, match="directory fsync failed"):
        save(path, plan, validation="unchecked")
