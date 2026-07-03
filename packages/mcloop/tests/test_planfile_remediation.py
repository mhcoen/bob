"""Regression tests for T-000035: ``PlanNotCanonicalError`` remediation.

The error's message used to say "Run: bob-plan migrate <path>", but
bob-plan has no ``migrate`` subcommand (choices are
validate/next/fmt/done/fail). These tests pin the repaired contract:

* the message names ``bob-plan fmt`` (a subcommand that exists), never
  ``bob-plan migrate``;
* the named command, run exactly as printed, canonicalizes the file the
  error was raised for — including the previously-unfixable case of a
  magic-lined ("marker-bearing") file with bare checkboxes, where
  ``bob-plan fmt`` used to die in the strict parse before it could
  assign ids;
* for the R1 (grammar-narrowed) case, where no tool can recover the
  dropped tasks, the message instructs moving the strays under a
  ``## Stage`` / ``## Phase`` heading first, and ``bob-plan fmt``
  refuses to save (file untouched) until that is done.

The command is exercised through :func:`bob_tools.planfile.cli.main`
with the subcommand token extracted from the message itself, so a
message naming a nonexistent subcommand fails here (argparse exits on
an unknown choice) rather than at a user's shell.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from bob_tools.planfile import parse_plan
from bob_tools.planfile.cli import EXIT_INVALID_PLAN, EXIT_OK
from bob_tools.planfile.cli import main as bob_plan_main

from mcloop._planfile_precondition import (
    PlanNotCanonicalError,
    enforce_canonical,
)

# The observed 2026-07-03 shape: magic line present, tasks parse (they
# sit under a Stage heading) but carry no T-NNNNNN ids. Only a lenient
# (non-magic-strict) parse surfaces this shape as a Plan; that is the
# loose read path BUGS.md-style queues and duplo use.
_MARKER_BEARING_ID_LESS = (
    "<!-- bob-plan-format: 1 -->\n"
    "\n"
    "# Demo\n"
    "\n"
    "## Stage 1: Setup\n"
    "<!-- phase_id: phase_001 -->\n"
    "\n"
    "- [ ] first task\n"
    "- [ ] second task\n"
)

# Classic R1 trap: an incomplete checkbox outside any Stage/Phase
# heading is invisible to the canonical parser.
_PHASELESS = "# Demo\n\n- [ ] stray task\n"

_RUN_LINE_RE = re.compile(r"run: bob-plan (?P<sub>\S+) (?P<target>\S+)", re.IGNORECASE)


def _raise_for(path: Path, *, lenient: bool) -> PlanNotCanonicalError:
    text = path.read_text()
    plan = parse_plan(text, source_path=path, force_strict_from_magic=not lenient)
    with pytest.raises(PlanNotCanonicalError) as ei:
        enforce_canonical(text, plan, source_path=path)
    return ei.value


def test_r2_message_names_existing_command_that_canonicalizes_the_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "PLAN.md"
    path.write_text(_MARKER_BEARING_ID_LESS)
    error = _raise_for(path, lenient=True)
    message = str(error)

    assert "bob-plan migrate" not in message
    match = _RUN_LINE_RE.search(message)
    assert match is not None, f"no 'run: bob-plan ...' line in message:\n{message}"
    assert match.group("target") == str(path)

    # Run the command exactly as the message names it. An unknown
    # subcommand would SystemExit inside argparse; a failing fmt would
    # return nonzero. Both fail this test.
    rc = bob_plan_main([match.group("sub"), match.group("target")])
    assert rc == EXIT_OK

    # The file it was raised for is now canonical: the strict
    # (magic-line-enforced) parse succeeds and the precondition passes.
    canonical_text = path.read_text()
    canonical_plan = parse_plan(canonical_text, source_path=path)
    enforce_canonical(canonical_text, canonical_plan, source_path=path)
    assert "T-000001" in canonical_text
    assert "T-000002" in canonical_text
    assert "<!-- bob-plan-format: 1 -->" in canonical_text


def test_r1_message_instructs_move_then_fmt_and_fmt_refuses_until_moved(
    tmp_path: Path,
) -> None:
    path = tmp_path / "PLAN.md"
    path.write_text(_PHASELESS)
    error = _raise_for(path, lenient=False)
    message = str(error)

    assert "bob-plan migrate" not in message
    assert "## Stage" in message or "## Phase" in message
    match = _RUN_LINE_RE.search(message)
    assert match is not None, f"no 'run: bob-plan ...' line in message:\n{message}"
    assert match.group("target") == str(path)

    # Running the command WITHOUT first moving the strays must refuse
    # rather than silently drop the task from the rewritten file.
    before = path.read_text()
    rc = bob_plan_main([match.group("sub"), match.group("target")])
    assert rc == EXIT_INVALID_PLAN
    assert path.read_text() == before

    # Following the instruction (move the stray under a heading), the
    # same command canonicalizes the file.
    path.write_text("# Demo\n\n## Stage 1: Setup\n\n- [ ] stray task\n")
    rc = bob_plan_main([match.group("sub"), match.group("target")])
    assert rc == EXIT_OK
    text = path.read_text()
    plan = parse_plan(text, source_path=path)
    enforce_canonical(text, plan, source_path=path)


def test_message_uses_placeholder_when_source_path_unknown() -> None:
    text = "- [ ] stray\n"
    plan = parse_plan(text)
    with pytest.raises(PlanNotCanonicalError) as ei:
        enforce_canonical(text, plan)
    message = str(ei.value)
    assert "bob-plan fmt <path>" in message
    assert "bob-plan migrate" not in message
