"""Stage 2 verification: compat reads plus repository green checks.

Run as ``python -m bob_tools.planfile.tests.manual.verify_stage2``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from bob_tools.planfile import parse_plan

REPO = Path("/Users/mhcoen/proj/bob-tools")
PATHS: tuple[str, ...] = (
    "/Users/mhcoen/proj/duplo/PLAN.md",
    "/Users/mhcoen/proj/mcloop/PLAN.md",
    "/Users/mhcoen/proj/mcloop/PLAN.EXAMPLE.md",
)
CHECKS: tuple[tuple[str, ...], ...] = (
    ("/Users/mhcoen/proj/bob-tools/.venv/bin/ruff", "check", "."),
    ("/Users/mhcoen/proj/bob-tools/.venv/bin/pytest", "-q"),
    ("/Users/mhcoen/proj/bob-tools/.venv/bin/mypy", "--strict", "bob_tools"),
)


def _verify_reads() -> int:
    failures = 0
    for path_str in PATHS:
        path = Path(path_str)
        try:
            plan = parse_plan(path.read_text())
        except Exception as exc:
            failures += 1
            print(f"FAIL {path_str} {type(exc).__name__}: {exc}")
            continue
        bugs = "true" if plan.bugs is not None else "false"
        print(f"OK {path_str} phases={len(plan.phases)} bugs={bugs}")
    return failures


def _run_checks() -> int:
    failures = 0
    for command in CHECKS:
        print(f"RUN {' '.join(command)}")
        result = subprocess.run(command, cwd=REPO, text=True)
        if result.returncode == 0:
            print(f"PASS {' '.join(command)}")
            continue
        failures += 1
        print(f"FAIL {' '.join(command)} exit={result.returncode}")
    return failures


def main() -> int:
    failures = _verify_reads()
    failures += _run_checks()
    if failures:
        print(f"SUMMARY FAIL failures={failures}")
        return 1
    print("SUMMARY PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
