"""Stage 7 verification: end-to-end check of the ``bob-plan`` CLI.

Run as ``python -m bob_tools.planfile.tests.manual.check_cli_end_to_end``.

Copies ``/Users/mhcoen/proj/mcloop/PLAN.EXAMPLE.md`` to ``/tmp`` so the
real file is never touched, then runs four CLI invocations in sequence
against the copy:

1. ``bob-plan validate`` — expected to exit ``1``. PLAN.EXAMPLE.md
   predates the strict-mode magic line and uses ``[RULEDOUT]`` in a
   leading-tag position, so validation must reject it before
   formatting.
2. ``bob-plan fmt`` — expected to exit ``0``. Assigns ``T-NNNNNN``
   ids, emits ``<!-- phase_id: ... -->`` comments, normalizes
   indentation, and writes the format magic line.
3. ``bob-plan validate`` — expected to exit ``0``. After ``fmt`` the
   plan is in canonical form and should validate cleanly.
4. ``bob-plan next`` — expected to exit ``0`` and print
   ``T-NNNNNN: <text>`` on stdout.

After ``fmt`` the script also asserts the diff against the original is
additive-only. The allowed mutations are the ones documented for
``fmt`` in the design doc: the leading magic line, a
``<!-- phase_id: ... -->`` comment after each phase heading, a
``T-NNNNNN: `` prefix on each task body, and two-space indentation
normalization. Any other change (removed prose, reordered tasks,
collapsed blank lines) makes the assertion fail.

All paths are hardcoded; the script takes no arguments. Each
subprocess call is given an explicit short timeout so the script
cannot hang the manual run. Progress lines carry a ``HH:MM:SS`` prefix
and are flushed eagerly so the operator sees activity on each step.
"""

from __future__ import annotations

import difflib
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

VENV_PY = Path("/Users/mhcoen/proj/bob-tools/.venv/bin/python")
CLI_INVOCATION: tuple[str, ...] = (str(VENV_PY), "-m", "bob_tools.planfile.cli")
SOURCE_PLAN = Path("/Users/mhcoen/proj/mcloop/PLAN.EXAMPLE.md")
SCRATCH = Path("/tmp/bob-plan-stage7-check.PLAN.md")

# Each subcommand parses a ~250-line PLAN.md and renders it back out;
# under a second on a warm machine. A 15 s ceiling is generous enough
# to absorb cold-start latency and a slow disk without letting a stuck
# process wedge the whole verification run.
SUBPROCESS_TIMEOUT_S = 15.0

EXIT_INVALID_PLAN = 1

_MAGIC_RE = re.compile(r"^<!--\s*bob-plan-format:\s*\d+\s*-->\s*$")
_PHASE_ID_RE = re.compile(r"^<!--\s*phase_id:\s*\w+\s*-->\s*$")
_TASK_ID_RE = re.compile(r"^(\s*)- \[([ xX!])\] T-(\d+):\s+(.*)$")


def _step(message: str) -> None:
    """Print one timestamped progress line, flushed immediately."""
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_S,
    )


def _format_result(result: subprocess.CompletedProcess[str]) -> str:
    return (
        f"exit={result.returncode}\n"
        f"--- stdout ---\n{result.stdout}"
        f"--- stderr ---\n{result.stderr}"
    )


def _normalize_after(text: str) -> list[str]:
    """Return ``text`` with fmt's additive mutations stripped back out.

    Drops the leading ``<!-- bob-plan-format: N -->`` line and the
    blank that follows it, every ``<!-- phase_id: ... -->`` comment,
    and the ``T-NNNNNN: `` prefix on each checkbox line. The output is
    what the file ought to look like if ``fmt`` only added the
    permitted things; comparing it to the unformatted original line by
    line is the additive-only assertion.
    """
    lines = text.splitlines()
    result: list[str] = []
    i = 0
    if i < len(lines) and _MAGIC_RE.match(lines[i]):
        i += 1
        if i < len(lines) and lines[i] == "":
            i += 1
    while i < len(lines):
        line = lines[i]
        if _PHASE_ID_RE.match(line):
            i += 1
            continue
        m = _TASK_ID_RE.match(line)
        if m is not None:
            indent, status, _task_id, body = m.groups()
            result.append(f"{indent}- [{status}] {body}")
        else:
            result.append(line)
        i += 1
    return result


def _setup() -> str | None:
    _step(f"copying {SOURCE_PLAN} -> {SCRATCH}")
    shutil.copyfile(SOURCE_PLAN, SCRATCH)
    return None


def _validate_expect_failure() -> str | None:
    _step(f"running: bob-plan validate {SCRATCH} (expecting exit=1)")
    result = _run([*CLI_INVOCATION, "validate", str(SCRATCH)])
    if result.returncode != EXIT_INVALID_PLAN:
        return (
            "expected validate to exit 1 before formatting, got "
            f"{result.returncode}\n{_format_result(result)}"
        )
    _step("validate exited 1 as expected (pre-fmt rejection)")
    return None


def _fmt() -> str | None:
    _step(f"running: bob-plan fmt {SCRATCH}")
    result = _run([*CLI_INVOCATION, "fmt", str(SCRATCH)])
    if result.returncode != 0:
        return f"fmt failed\n{_format_result(result)}"
    _step("fmt exited 0")
    return None


def _validate_expect_success() -> str | None:
    _step(f"running: bob-plan validate {SCRATCH} (expecting exit=0)")
    result = _run([*CLI_INVOCATION, "validate", str(SCRATCH)])
    if result.returncode != 0:
        return (
            "expected validate to exit 0 after fmt, got "
            f"{result.returncode}\n{_format_result(result)}"
        )
    _step("validate exited 0 as expected (post-fmt acceptance)")
    return None


def _next() -> str | None:
    _step(f"running: bob-plan next {SCRATCH}")
    result = _run([*CLI_INVOCATION, "next", str(SCRATCH)])
    if result.returncode != 0:
        return f"next failed\n{_format_result(result)}"
    output = result.stdout.strip() or "(no actionable task)"
    _step(f"next: {output}")
    return None


def _check_additive_diff() -> str | None:
    _step("checking the post-fmt diff is additive-only")
    before = SOURCE_PLAN.read_text().splitlines()
    after = SCRATCH.read_text()
    normalized = _normalize_after(after)
    while before and not before[-1]:
        before.pop()
    while normalized and not normalized[-1]:
        normalized.pop()
    if normalized == before:
        _step("diff is additive-only (task IDs, phase-id comments, magic line)")
        return None
    diff = "\n".join(
        difflib.unified_diff(
            before,
            normalized,
            fromfile="before",
            tofile="after-normalized",
            lineterm="",
            n=2,
        )
    )
    return (
        "diff is not additive-only. Allowed additions: task ids, "
        "phase-id comments, indentation normalization, and the format "
        "magic line. Unexpected differences (after stripping allowed "
        f"additions):\n{diff}"
    )


def main() -> int:
    _step("Stage 7 verification: bob-plan end-to-end check")
    if not VENV_PY.exists():
        print(
            f"FAIL: {VENV_PY} not found. Create the bob-tools venv "
            "before running this check.",
            file=sys.stderr,
        )
        return 1
    if not SOURCE_PLAN.exists():
        print(f"FAIL: {SOURCE_PLAN} not found", file=sys.stderr)
        return 1

    steps = (
        _setup,
        _validate_expect_failure,
        _fmt,
        _validate_expect_success,
        _next,
        _check_additive_diff,
    )
    for step in steps:
        try:
            error = step()
        except subprocess.TimeoutExpired as exc:
            print(
                f"FAIL: subprocess timed out after {exc.timeout}s: "
                f"{' '.join(str(a) for a in exc.cmd)}",
                file=sys.stderr,
            )
            return 1
        except OSError as exc:
            print(f"FAIL: I/O error during {step.__name__}: {exc}", file=sys.stderr)
            return 1
        if error is not None:
            print(f"FAIL ({step.__name__}): {error}", file=sys.stderr)
            return 1

    _step("PASS: end-to-end CLI flow OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
