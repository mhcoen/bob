"""Error/crash handling: check errors.json, diagnose, insert fix tasks."""

from __future__ import annotations

import hashlib
import json as _json
import subprocess
from pathlib import Path

from mcloop import formatting
from mcloop.prompts import parse_diagnostic_output
from mcloop.runner import run_diagnostic

_MAX_FIX_ATTEMPTS = 3


def _error_signature_hash(entry: dict) -> str:
    """Compute a stable hash for an error entry's signature.

    Uses exception_type + source_file + line as the signature.
    Falls back to stack_trace or description if location fields
    are missing.
    """
    exc_type = entry.get("exception_type", "")
    source = entry.get("source_file", "")
    line_num = str(entry.get("line", ""))
    if exc_type and (source or line_num):
        sig = f"{exc_type}:{source}:{line_num}"
    elif entry.get("stack_trace"):
        sig = entry["stack_trace"]
    else:
        sig = f"{exc_type}:{entry.get('description', '')}"
    return hashlib.sha256(sig.encode()).hexdigest()[:16]


def _check_errors_json(
    project_dir: Path,
    model: str | None = None,
) -> bool:
    """Check for .mcloop/errors.json and prompt the user to fix bugs.

    Reads the error file, prints a summary, and asks the user whether
    to run diagnostic sessions and insert fix tasks into a ``## Bugs``
    section of PLAN.md. Returns True if tasks were added (or no errors
    found), False if the user declined or all errors are unresolvable.

    Tracks fix_attempts per error via a hash of the error signature.
    If any error has been diagnosed ``_MAX_FIX_ATTEMPTS`` or more times,
    it is marked unresolvable and skipped. If ALL errors are unresolvable,
    prints context and returns False.
    """
    errors_path = project_dir / ".mcloop" / "errors.json"
    if not errors_path.is_file():
        return True
    try:
        entries = _json.loads(errors_path.read_text())
    except (OSError, _json.JSONDecodeError):
        return True
    if not isinstance(entries, list) or not entries:
        return True

    # Classify entries by fix_attempts
    resolvable: list[dict] = []
    unresolvable: list[dict] = []
    for entry in entries:
        attempts = entry.get("fix_attempts", 0)
        if not isinstance(attempts, int):
            attempts = 0
        if attempts >= _MAX_FIX_ATTEMPTS:
            unresolvable.append(entry)
        else:
            resolvable.append(entry)

    # Print unresolvable errors
    if unresolvable:
        print(
            formatting.error_msg(
                f"{len(unresolvable)} error(s) exceeded "
                f"{_MAX_FIX_ATTEMPTS} fix attempts — unresolvable:"
            ),
            flush=True,
        )
        for i, entry in enumerate(unresolvable, 1):
            exc_type = entry.get("exception_type", "Unknown")
            desc = entry.get("description", "")
            source = entry.get("source_file", "")
            line = entry.get("line", "")
            location = f" at {source}:{line}" if source else ""
            attempts = entry.get("fix_attempts", 0)
            short_desc = desc[:80] + "..." if len(desc) > 80 else desc
            print(
                f"  {i}. {exc_type}: {short_desc}{location}  (attempted {attempts}x)",
                flush=True,
            )

    # If ALL are unresolvable, stop
    if not resolvable:
        print(
            formatting.error_msg(
                "All errors are unresolvable. "
                "Review the bugs manually and clear .mcloop/errors.json to retry."
            ),
            flush=True,
        )
        return False

    # Print summary of resolvable errors
    print(
        formatting.error_msg(f"Found {len(resolvable)} bug(s) in .mcloop/errors.json:"),
        flush=True,
    )
    for i, entry in enumerate(resolvable, 1):
        exc_type = entry.get("exception_type", "Unknown")
        desc = entry.get("description", "")
        ts = entry.get("timestamp", "")
        source = entry.get("source_file", "")
        line = entry.get("line", "")
        location = f" at {source}:{line}" if source else ""
        # Truncate description for display
        short_desc = desc[:80] + "..." if len(desc) > 80 else desc
        ts_display = f"  [{ts}]" if ts else ""
        print(
            f"  {i}. {exc_type}: {short_desc}{location}{ts_display}",
            flush=True,
        )

    # Ask user
    try:
        answer = input("\nFix these bugs before continuing? [Y/n] ")
    except (EOFError, KeyboardInterrupt):
        print(flush=True)
        return False
    if answer.strip().lower() in ("n", "no"):
        return True

    # Prepend fix tasks to PLAN.md
    plan_path = project_dir / "PLAN.md"
    if not plan_path.is_file():
        print(
            formatting.error_msg("No PLAN.md found, cannot add tasks"),
            flush=True,
        )
        return False

    # Gather git log for diagnostic context
    git_log = ""
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "-20"],
            cwd=project_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            git_log = result.stdout.strip()
    except Exception:
        pass

    # Run diagnostic sessions per resolvable error
    log_dir = project_dir / "logs"
    task_lines: list[str] = []
    for i, entry in enumerate(resolvable, 1):
        exc_type = entry.get("exception_type", "Unknown")
        desc = entry.get("description", "")
        source_file = entry.get("source_file", "")
        line = entry.get("line", "")
        location = f" at {source_file}:{line}" if source_file else ""

        # Read relevant source file
        source_content = ""
        if source_file:
            source_path = project_dir / source_file
            if source_path.is_file():
                try:
                    source_content = source_path.read_text()
                except OSError:
                    pass

        print(
            formatting.system_msg(f"Diagnosing {i}/{len(resolvable)}: {exc_type}{location}"),
            flush=True,
        )

        result = run_diagnostic(
            project_dir,
            log_dir,
            entry,
            source_content=source_content,
            git_log=git_log,
            model=model,
        )

        fix_desc = ""
        if result.success:
            fix_desc = parse_diagnostic_output(result.output)

        if fix_desc:
            task_lines.append(f"- [ ] {fix_desc}")
        else:
            # Fallback to generic description
            short_desc = desc[:120] + "..." if len(desc) > 120 else desc
            task_lines.append(f"- [ ] Fix crash: {exc_type}: {short_desc}{location}")

        # Increment fix_attempts
        prev = entry.get("fix_attempts", 0)
        if not isinstance(prev, int):
            prev = 0
        entry["fix_attempts"] = prev + 1

    # Write back updated entries (resolvable + unresolvable)
    try:
        errors_path.write_text(_json.dumps(resolvable + unresolvable, indent=2))
    except OSError:
        pass

    # Insert tasks under ## Bugs section
    _insert_bugs_section(plan_path, task_lines)

    print(
        formatting.system_msg(f"Added {len(resolvable)} fix task(s) to PLAN.md"),
        flush=True,
    )
    return True


def _insert_bugs_section(plan_path: Path, task_lines: list[str]) -> None:
    """Insert tasks into a ``## Bugs`` section of PLAN.md.

    If a ``## Bugs`` section already exists, appends tasks to it.
    Otherwise, inserts a new section before the first ``## Stage``
    header or the first checkbox line.
    """
    import re as _re

    plan_text = plan_path.read_text()
    lines = plan_text.splitlines(keepends=True)
    task_block = "\n".join(task_lines) + "\n"

    # Check if ## Bugs section already exists
    bugs_header_re = _re.compile(r"^##\s+Bugs\s*$", _re.IGNORECASE)
    stage_header_re = _re.compile(r"^##\s+Stage\s+\d+", _re.IGNORECASE)
    bugs_idx = None
    for idx, raw_line in enumerate(lines):
        if bugs_header_re.match(raw_line.strip()):
            bugs_idx = idx
            break

    if bugs_idx is not None:
        # Find end of Bugs section (next ## header or end of file)
        insert_at = len(lines)
        for idx in range(bugs_idx + 1, len(lines)):
            if lines[idx].strip().startswith("## "):
                insert_at = idx
                break
        lines.insert(insert_at, task_block)
        plan_path.write_text("".join(lines))
        return

    # No existing ## Bugs section — create one
    bugs_section = f"## Bugs\n\n{task_block}\n"

    # Insert before the first ## Stage header
    for idx, raw_line in enumerate(lines):
        if stage_header_re.match(raw_line.strip()):
            lines.insert(idx, bugs_section)
            plan_path.write_text("".join(lines))
            return

    # No stage headers — insert before the first checkbox
    from mcloop.checklist import CHECKBOX_RE

    for idx, raw_line in enumerate(lines):
        if CHECKBOX_RE.match(raw_line):
            lines.insert(idx, bugs_section)
            plan_path.write_text("".join(lines))
            return

    # No checkboxes — append to end
    if not plan_text.endswith("\n"):
        plan_text += "\n"
    plan_path.write_text(plan_text + bugs_section)
