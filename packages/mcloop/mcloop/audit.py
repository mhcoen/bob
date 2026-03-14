"""Audit-related functions extracted from main.py."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from mcloop import formatting
from mcloop.checks import run_checks
from mcloop.git_ops import _commit, _get_diff, _get_git_hash, _git, _has_meaningful_changes
from mcloop.notify import notify
from mcloop.prompts import (
    bugs_md_has_bugs,
    parse_bugs_md,
    parse_verification_output,
    review_found_problems,
)
from mcloop.runner import run_audit, run_bug_fix, run_bug_verify, run_post_fix_review


def _format_elapsed(seconds: float) -> str:
    """Format elapsed seconds as human-readable string."""
    m, s = divmod(int(seconds), 60)
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def _tail(text: str, max_lines: int = 50) -> str:
    """Return the last N lines of text."""
    lines = text.strip().splitlines()
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
    return "\n".join(lines)


def _print_error_tail(output: str, max_lines: int = 30) -> None:
    """Print the last N lines of output to help diagnose failures."""
    lines = output.strip().splitlines()
    tail = lines[-max_lines:] if len(lines) > max_lines else lines
    if tail:
        print("    --- last output ---", flush=True)
        for line in tail:
            print(f"    {line}", flush=True)
        print("    ---", flush=True)


AUDIT_HASH_FILE = ".mcloop-last-audit"


def _should_skip_audit(project_dir: Path) -> bool:
    """Skip audit if no source files changed since last audit."""
    if not (project_dir / ".git").exists():
        return False
    hash_file = project_dir / AUDIT_HASH_FILE
    if not hash_file.exists():
        return False
    last_hash = hash_file.read_text().strip()
    if not last_hash:
        return False
    result = _git(
        ["git", "diff", "--name-only", last_hash, "HEAD"],
        cwd=project_dir,
        label="audit diff check",
    )
    if result.returncode != 0:
        return False
    changed = [
        f
        for f in result.stdout.strip().splitlines()
        if f and not f.startswith("logs/") and f != "PLAN.md" and f != AUDIT_HASH_FILE
    ]
    return len(changed) == 0


def _save_audit_hash(project_dir: Path) -> None:
    """Write current HEAD hash to .mcloop-last-audit."""
    h = _get_git_hash(project_dir)
    if h:
        (project_dir / AUDIT_HASH_FILE).write_text(h + "\n")


def _run_audit_fix_cycle(
    project_dir: Path,
    log_dir: Path,
    model: str | None = None,
) -> None:
    """Run two rounds of audit/verify/fix to catch bugs introduced by fixes."""
    if _should_skip_audit(project_dir):
        print(
            formatting.system_msg("Audit skipped (no changes since last audit)"),
            flush=True,
        )
        return

    max_rounds = 2
    for round_num in range(1, max_rounds + 1):
        print(
            formatting.system_msg(f"Audit round {round_num}/{max_rounds}"),
            flush=True,
        )
        fixed = _run_single_audit_round(
            project_dir,
            log_dir,
            model=model,
        )
        if not fixed:
            # No bugs found or fixed — no need for another round
            if round_num == 1:
                notify("Audit complete: no bugs found.")
            else:
                notify("Audit complete: fixes verified, no new bugs.")
            break
        if round_num == max_rounds:
            notify("Audit complete: bugs fixed.")

    _save_audit_hash(project_dir)


def _run_single_audit_round(
    project_dir: Path,
    log_dir: Path,
    model: str | None = None,
) -> bool:
    """Run one audit/verify/fix cycle. Returns True if bugs were fixed."""
    bugs_path = project_dir / "BUGS.md"

    # Resume from existing BUGS.md if present
    if bugs_path.exists():
        bugs_content = bugs_path.read_text()
        if bugs_md_has_bugs(bugs_content):
            print(
                formatting.system_msg("Found existing BUGS.md, resuming fix cycle..."),
                flush=True,
            )
        else:
            print(
                formatting.system_msg("Existing BUGS.md has no bugs"),
                flush=True,
            )
            bugs_path.unlink()
            return False
    else:
        print(formatting.system_msg("Running bug audit..."), flush=True)
        _audit_start = time.monotonic()
        audit_result = run_audit(
            project_dir,
            log_dir,
            model=model,
            existing_bugs="",
        )
        _audit_el = _format_elapsed(time.monotonic() - _audit_start)
        if not audit_result.success:
            print(
                f"audit: session exited with code {audit_result.exit_code}, skipping fix",
                flush=True,
            )
            return False

        if not bugs_path.exists():
            print(
                f"audit: BUGS.md not written, skipping fix [{_audit_el}]",
                flush=True,
            )
            return False

        bugs_content = bugs_path.read_text()
        if not bugs_md_has_bugs(bugs_content):
            print(f"audit: no bugs found [{_audit_el}]", flush=True)
            bugs_path.unlink()
            return False

    # Pre-fix verification: check each bug against source code
    bugs_content = bugs_path.read_text()
    parsed_bugs = parse_bugs_md(bugs_content)
    if parsed_bugs:
        print(
            formatting.system_msg(f"Verifying {len(parsed_bugs)} bugs..."),
            flush=True,
        )
        _verify_start = time.monotonic()
        verify_result = run_bug_verify(
            project_dir,
            log_dir,
            bugs_content,
            model=model,
        )
        _verify_el = _format_elapsed(time.monotonic() - _verify_start)
        if verify_result.success:
            verdicts = parse_verification_output(
                verify_result.output,
            )
            for status, header, reason in verdicts:
                if status == "CONFIRMED":
                    print(
                        f"  CONFIRMED: {header}",
                        flush=True,
                    )
                else:
                    suffix = f" ({reason})" if reason else ""
                    print(
                        f"  REMOVED: {header}{suffix}",
                        flush=True,
                    )

            if verdicts:
                removed_headers = {h for s, h, _ in verdicts if s == "REMOVED"}
                # A bug is removed if any REMOVED verdict
                # matches its title (substring match).
                confirmed_bugs = [
                    b
                    for b in parsed_bugs
                    if not any(rh in b["title"] or b["title"] in rh for rh in removed_headers)
                ]
                if not confirmed_bugs:
                    print(
                        formatting.system_msg(
                            f"All reported bugs were false positives [{_verify_el}]"
                        ),
                        flush=True,
                    )
                    bugs_path.unlink(missing_ok=True)
                    return False
                if len(confirmed_bugs) < len(parsed_bugs):
                    new_content = "# Bugs\n\n"
                    for bug in confirmed_bugs:
                        new_content += bug["body"] + "\n\n"
                    bugs_path.write_text(new_content)
                    bugs_content = new_content

    max_fix_attempts = 3
    for attempt in range(1, max_fix_attempts + 1):
        print(
            formatting.system_msg(f"Fixing bugs (attempt {attempt}/{max_fix_attempts})..."),
            flush=True,
        )
        _fix_start = time.monotonic()
        fix_result = run_bug_fix(
            project_dir,
            log_dir,
            model=model,
        )
        _fix_el = _format_elapsed(time.monotonic() - _fix_start)

        if not fix_result.success:
            print(
                f"bug-fix: session exited with code {fix_result.exit_code}",
                flush=True,
            )
            break

        if not _has_meaningful_changes(project_dir):
            print(
                "bug-fix: no changes made",
                flush=True,
            )
            break

        check_result = run_checks(project_dir)
        if check_result.passed:
            # Post-fix review: verify changes don't introduce new bugs
            diff = _get_diff(project_dir)
            if diff:
                print(
                    formatting.system_msg("Post-fix review..."),
                    flush=True,
                )
                _review_start = time.monotonic()
                review_result = run_post_fix_review(
                    project_dir,
                    log_dir,
                    bugs_content,
                    diff,
                    model=model,
                )
                _review_el = _format_elapsed(time.monotonic() - _review_start)
                if review_result.success:
                    found, desc = review_found_problems(
                        review_result.output,
                    )
                    if found:
                        print(
                            formatting.error_msg("Post-fix review found problems"),
                            flush=True,
                        )
                        for line in desc.splitlines()[:10]:
                            print(f"    {line}", flush=True)
                        bugs_content = bugs_content + "\n\n## Post-fix review problems\n" + desc
                        bugs_path.write_text(bugs_content)
                        continue
                    print(
                        formatting.system_msg(
                            f"Post-fix review: no new bugs introduced [{_review_el}]"
                        ),
                        flush=True,
                    )

            try:
                _commit(project_dir, "Fix bugs from audit")
            except RuntimeError as exc:
                print(
                    formatting.error_msg(str(exc)),
                    flush=True,
                )
                sys.exit(1)
            bugs_path.unlink(missing_ok=True)
            return True

        error_ctx = f"Command: {check_result.command}\n" + _tail(check_result.output, 50)
        print(
            formatting.error_msg(f"Bug fix checks failed (attempt {attempt}/{max_fix_attempts})"),
            flush=True,
        )
        _print_error_tail(check_result.output)

        # Append error to BUGS.md so next attempt sees it
        bugs_path.write_text(
            bugs_content + "\n\n## Post-fix check failure\n" + error_ctx,
        )

    return False
