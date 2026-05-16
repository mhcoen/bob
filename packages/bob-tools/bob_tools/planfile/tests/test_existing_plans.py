"""Round-trip every existing PLAN.md on this machine through fmt.

Stage 8 acceptance: the fmt composition (parse, migrate, render) must
reach a fixed point on the real PLAN.md files in sibling projects.
Concretely, for each source file:

1. Read the bytes into memory.
2. ``parse_plan`` (compat mode, since today's files predate the
   strict-mode magic line).
3. ``migrate`` — assigns ``T-NNNNNN`` ids and ``<!-- phase_id: ... -->``
   comments so the document satisfies the strict-mode shape.
4. ``render_plan`` — the **first** render, canonical text.
5. ``parse_plan(..., strict=True)`` on the first render — strict is
   safe now because step 3 added the structural identifiers strict
   mode requires.
6. ``render_plan`` again — the **second** render.

The assertion is that the second render equals the first byte-for-byte.
That is the canonical-form fixed-point property on real-world input.

Source files are read once via :meth:`pathlib.Path.read_text` and every
subsequent step operates on the resulting Python objects; nothing in
this test writes back to disk. The post-condition assertion (see
``test_source_files_are_untouched``) verifies the bytes on disk are
identical before and after the round-trip run, so a future regression
that accidentally re-introduces file mutation cannot slip past this
suite.
"""

from __future__ import annotations

import difflib
import hashlib
from pathlib import Path

import pytest

from bob_tools.planfile import migrate, parse_plan, render_plan

SOURCE_PATHS: tuple[Path, ...] = (
    Path("/Users/mhcoen/proj/duplo/PLAN.md"),
    Path("/Users/mhcoen/proj/mcloop/PLAN.md"),
    Path("/Users/mhcoen/proj/mcloop/PLAN.EXAMPLE.md"),
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    "source_path",
    SOURCE_PATHS,
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_existing_plan_fmt_is_fixed_point(source_path: Path) -> None:
    if not source_path.is_file():
        pytest.skip(
            f"source PLAN.md not present at {source_path}; "
            "this round-trip check only runs in the dev environment "
            "where the sibling projects are checked out"
        )
    text = source_path.read_text()
    plan = parse_plan(text)
    migrated = migrate(plan)
    first_render = render_plan(migrated)
    re_parsed = parse_plan(first_render, strict=True)
    second_render = render_plan(re_parsed)
    if first_render != second_render:
        diff = "".join(
            difflib.unified_diff(
                first_render.splitlines(keepends=True),
                second_render.splitlines(keepends=True),
                fromfile=f"{source_path.name} (first render)",
                tofile=f"{source_path.name} (second render)",
            )
        )
        raise AssertionError(
            f"fmt is not a fixed point on {source_path}: "
            "render(parse_strict(render(migrate(parse(...))))) differs "
            "from the first render. Unified diff "
            "(first render -> second render):\n" + diff
        )


def test_source_files_are_untouched() -> None:
    """Round-tripping the real PLAN.md files must not modify them on disk.

    Reads each source file's SHA-256 once, runs the same parse-migrate-
    render-parse-render pipeline ``test_existing_plan_fmt_is_fixed_point``
    runs, then re-reads the SHA-256. Any divergence means a step in the
    pipeline acquired write access to the source file and would corrupt
    the user's working state.
    """
    missing = [p for p in SOURCE_PATHS if not p.is_file()]
    if missing:
        pytest.skip(
            "source PLAN.md(s) not present: "
            f"{', '.join(str(p) for p in missing)}; "
            "this on-disk-hash guard only runs in the dev environment "
            "where the sibling projects are checked out"
        )
    digests_before = {p: _digest(p) for p in SOURCE_PATHS}
    for path in SOURCE_PATHS:
        text = path.read_text()
        plan = parse_plan(text)
        migrated = migrate(plan)
        first_render = render_plan(migrated)
        parse_plan(first_render, strict=True)
        render_plan(parse_plan(first_render, strict=True))
    digests_after = {p: _digest(p) for p in SOURCE_PATHS}
    assert digests_before == digests_after, (
        "round-tripping modified one or more source files on disk: "
        f"{ {p: (digests_before[p], digests_after[p]) for p in SOURCE_PATHS} }"
    )
