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
import re
from pathlib import Path

import pytest

from bob_tools.planfile import migrate, parse_plan, render_plan
from bob_tools.planfile.operations import validate_plan

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


@pytest.mark.parametrize(
    "source_path",
    SOURCE_PATHS,
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_existing_plan_fmt_output_validates(source_path: Path) -> None:
    """fmt output of every real PLAN.md must pass strict ``validate_plan``.

    Closes the fmt -> validate fixed-point gap that
    :func:`test_existing_plan_fmt_is_fixed_point` does not cover: that
    test only checks that two successive renders agree, which a plan
    can satisfy while still containing a construct the validator
    rejects. The `[RULEDOUT]` defect (mcloop/PLAN.EXAMPLE.md:243 task
    body begins with the literal token) round-tripped cleanly through
    parse -> migrate -> render -> parse(strict) -> render but failed
    strict validation with "unknown bracket tag [RULEDOUT]", surfacing
    only when the manual Stage 7 verifier exercised the CLI end-to-end.
    Asserting validate_plan on the rendered output here is the unit-
    test analogue of that verifier's invariant.
    """
    if not source_path.is_file():
        pytest.skip(
            f"source PLAN.md not present at {source_path}; "
            "this fmt -> validate check only runs in the dev environment "
            "where the sibling projects are checked out"
        )
    text = source_path.read_text()
    plan = parse_plan(text)
    migrated = migrate(plan)
    rendered = render_plan(migrated)
    re_parsed = parse_plan(rendered, strict=True)
    validate_plan(re_parsed)


@pytest.mark.parametrize(
    "source_path",
    SOURCE_PATHS,
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_existing_plan_fmt_is_lossless(source_path: Path) -> None:
    """fmt of every real PLAN.md must preserve every source non-blank line.

    Lossless invariant per design doc section 3.2 (planfile.md:268):
    ``canonicalize(text)`` is lossless formatting. ``bob-plan fmt`` is
    ``save(path, migrate(parse_plan(read(path))))``; ``migrate`` adds
    structural identifiers (T-IDs, ``<!-- phase_id: ... -->`` comments)
    so the post-fmt text contains every source non-blank line plus
    those additions. A pre-fix renderer dropped non-checkbox prose
    continuation lines (e.g. mcloop/PLAN.EXAMPLE.md:111-116 example-
    flow bullets) and intra-section blank-line groupings; this test
    asserts every non-blank source line survives migration+render
    (modulo the leading ``T-NNNNNN: `` prefix the renderer adds to
    each checkbox line).

    Why blank-only comparison: blank-line positions can legitimately
    shift across the structural additions migrate introduces (a new
    phase-id comment shifts later lines down by one), so the strict
    losslessness check focuses on non-blank content. Blank-line
    preservation per se is exercised by the parse->render->parse
    fixed point in ``test_existing_plan_fmt_is_fixed_point`` and by
    the additive-only diff assertion in
    ``bob_tools/planfile/tests/manual/check_cli_end_to_end.py``.
    """
    if not source_path.is_file():
        pytest.skip(
            f"source PLAN.md not present at {source_path}; "
            "this losslessness check only runs in the dev environment "
            "where the sibling projects are checked out"
        )
    text = source_path.read_text()
    plan = parse_plan(text)
    migrated = migrate(plan)
    rendered = render_plan(migrated)
    # Compare on stripped lines. Legitimate canonical transformations
    # the renderer applies:
    #   - Two-space-per-level indentation (design doc section 4.2
    #     Notes; renderer.py:154-160).
    #   - ``T-NNNNNN:`` migration prefix on every checkbox line
    #     (design doc section 3.2 fmt = parse+migrate+save).
    #   - Heading-level normalization to ``##`` (the compat parser
    #     accepts any ``#+`` count for stage/phase headings via
    #     ``_STAGE_RE``; the renderer always emits two hashes).
    # These are explicit canonical changes — semantically identical,
    # byte-different. The losslessness invariant guards the
    # opposite case: source content the parser would otherwise drop.
    # The lost-content defect targets non-checkbox, non-heading
    # source lines: markdown sub-bullets, quoted example flow, code
    # blocks, free prose between tasks. Those must reappear in the
    # output, possibly stripped of indentation.
    checkbox_re = re.compile(r"^\s*- \[[ xX!]\] ")
    heading_re = re.compile(r"^\s*#+\s")
    rendered_stripped = {line.strip() for line in rendered.splitlines()}
    missing: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if checkbox_re.match(line):
            continue
        if heading_re.match(line):
            continue
        if line.strip() not in rendered_stripped:
            missing.append(line)
    assert not missing, (
        f"fmt is not lossless on {source_path}: "
        f"{len(missing)} non-checkbox non-heading source line(s) "
        f"absent from the formatted output (this is the prose-"
        f"continuation/non-task content the lossless invariant "
        f"guards). First few:\n" + "\n".join(missing[:5])
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
