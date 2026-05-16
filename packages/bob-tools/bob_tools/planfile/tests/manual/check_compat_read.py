"""Stage 2 manual verification: parse real PLAN.md files in compat mode.

Run as ``python -m bob_tools.planfile.tests.manual.check_compat_read``.

For each hard-coded path the script parses the file with
:func:`bob_tools.planfile.parse_plan` and prints exactly one line:

    OK   <path> phases=<n> bugs=<true|false> bug_count=<n>

When parsing raises, the line begins with ``FAIL`` and carries the
exception class plus its message:

    FAIL <path> <ExceptionClass>: <message>

The script exits non-zero if any path failed to parse, otherwise zero.

This replaces the long ``python -c "..."`` one-liner that task 2.8
originally used; that form produced ``bugs=False`` for files without a
Bugs section, which read as "the bugs section is empty" rather than
"no Bugs section is present" and confused the verification step. The
``bug_count`` field disambiguates: ``bugs=false bug_count=0`` is a
file with no Bugs section, while ``bugs=true bug_count=0`` would be a
present-but-empty Bugs section, and ``bugs=true bug_count=N`` is the
normal populated case.
"""

from __future__ import annotations

import sys
from pathlib import Path

from bob_tools.planfile import bug_count, parse_plan

PATHS: tuple[str, ...] = (
    "/Users/mhcoen/proj/duplo/PLAN.md",
    "/Users/mhcoen/proj/mcloop/PLAN.md",
    "/Users/mhcoen/proj/mcloop/PLAN.EXAMPLE.md",
)


def main() -> int:
    failures = 0
    for path_str in PATHS:
        path = Path(path_str)
        try:
            text = path.read_text()
            plan = parse_plan(text)
        except Exception as exc:
            failures += 1
            print(f"FAIL {path_str} {type(exc).__name__}: {exc}")
            continue
        phases = len(plan.phases)
        bugs_present = "true" if plan.bugs is not None else "false"
        bugs_n = bug_count(plan)
        print(f"OK   {path_str} phases={phases} bugs={bugs_present} bug_count={bugs_n}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
