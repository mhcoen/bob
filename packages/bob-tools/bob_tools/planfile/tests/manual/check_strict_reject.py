"""Stage 3 manual verification: real PLAN.md files must be rejected in strict mode.

Run as ``python -m bob_tools.planfile.tests.manual.check_strict_reject``.

The two hard-coded PLAN.md files predate the strict-mode magic line and
the stable-id syntax, so ``parse_plan(text, strict=True)`` is expected
to raise :class:`PlanSyntaxError` for each. For every path the script
prints exactly one line:

    REJECTED <path> at line=<n> col=<m>

A path that parses cleanly in strict mode is a regression; the script
prints

    PARSED   <path> (UNEXPECTED - strict mode should have rejected this)

and exits non-zero.
"""

from __future__ import annotations

import sys
from pathlib import Path

from bob_tools.planfile import PlanSyntaxError, parse_plan

PATHS: tuple[str, ...] = (
    "/Users/mhcoen/proj/duplo/PLAN.md",
    "/Users/mhcoen/proj/mcloop/PLAN.md",
)


def main() -> int:
    unexpected = 0
    for path_str in PATHS:
        text = Path(path_str).read_text()
        try:
            parse_plan(text, strict=True)
        except PlanSyntaxError as exc:
            print(f"REJECTED {path_str} at line={exc.line} col={exc.column}")
            continue
        unexpected += 1
        print(
            f"PARSED   {path_str} (UNEXPECTED - strict mode should have rejected this)"
        )
    return 1 if unexpected else 0


if __name__ == "__main__":
    sys.exit(main())
