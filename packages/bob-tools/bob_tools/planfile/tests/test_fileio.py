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
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from bob_tools.planfile import (
    ConcurrentUpdateError,
    Plan,
    fileio,
    load,
    parse_plan,
    save,
    update,
)

# Non-canonical fixture: missing the v1 magic preamble and the
# <!-- phase_id: ... --> comment, so it does not satisfy
# assert_mcloop_canonical. Used by tests whose purpose is atomic-write,
# lock, or crash behavior — those exercise the I/O path itself and
# must opt out of canonical validation (per T-000181) by passing
# validation="unchecked", otherwise the fixture would be rejected
# before any of the I/O contract under test ran.
_MINIMAL_PLAN = "# Stage 6 fixture\n\n## Stage 1: Smoke\n\n- [ ] T-000001: only task\n"

# Canonical fixture: contains the v1 magic preamble, a phase_id
# comment, and a T-NNNNNN id, so it passes
# assert_mcloop_canonical(validate_plan(constructed=True)). Used by
# the ordinary (no crash, no lock-race) save/update tests so they
# exercise the default validation="canonical" path.
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
    "- [ ] T-000001: only task\n"
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
    # crash behavior, not canonical-input enforcement. The
    # _MINIMAL_PLAN fixture is deliberately non-canonical so
    # validation="canonical" would raise before os.replace was ever
    # called, hiding the crash-safety contract we want to pin.
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
            # serialization, not canonical enforcement. The
            # _MINIMAL_PLAN fixture is non-canonical, so the default
            # canonical mode would reject the rendered output before
            # the lock-race assertions could observe winner/loser
            # behavior.
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
    # own contract. The _MINIMAL_PLAN fixture is non-canonical, so
    # the default canonical mode would raise before any lock was
    # acquired and the counting_acquire helper would observe zero
    # calls — flipping the assertion from "exactly one lock" to "no
    # lock", which is not the property under test.
    save(path, plan, validation="unchecked")

    assert calls == [path], (
        f"save() must acquire the exclusive lock exactly once for the "
        f"target path; got {calls}"
    )
