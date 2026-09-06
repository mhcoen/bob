"""REQ-SPAN-001 oracle v1: process-level cases, independent of project tests."""

import json
from pathlib import Path
import subprocess
import sys

CASES = (
    (2, 4, "3\n"),
    (4, 4, "1\n"),
    (-4, -2, "3\n"),
    (-2, 2, "5\n"),
    (4, 2, "0\n"),
    (0, 0, "1\n"),
    (1000000, 1000002, "3\n"),
)


def check(root):
    records = []
    commands = [("project-tests", [sys.executable, "regression.py"], None)]
    commands.extend(
        (
            f"span({start},{end})",
            [sys.executable, "-I", "span.py", str(start), str(end)],
            expected,
        )
        for start, end, expected in CASES
    )
    for name, argv, expected in commands:
        try:
            result = subprocess.run(
                argv, cwd=root, capture_output=True, text=True, timeout=10
            )
            records.append(
                {
                    "name": name,
                    "argv": argv,
                    "returncode": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "expected_stdout": expected,
                    "passed": result.returncode == 0
                    and (expected is None or result.stdout == expected),
                }
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            records.append(
                {
                    "name": name,
                    "argv": argv,
                    "returncode": None,
                    "passed": False,
                    "error": str(exc),
                }
            )
    return records


if __name__ == "__main__":
    records = check(Path(sys.argv[1]))
    print(json.dumps(records, indent=2))
    sys.exit(0 if all(record["passed"] for record in records) else 1)
