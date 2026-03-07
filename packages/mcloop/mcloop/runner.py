"""Run AI CLI subprocesses and capture output."""

from __future__ import annotations

import os
import queue
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class RunResult:
    success: bool
    output: str
    exit_code: int
    log_path: Path


def run_task(
    task_text: str,
    cli: str,
    project_dir: str | Path,
    log_dir: str | Path,
    description: str = "",
    task_label: str = "",
    model: str | None = None,
    prior_errors: str = "",
    session_context: str = "",
    check_commands: list[str] | None = None,
) -> RunResult:
    """Launch a CLI session to perform a task. Returns RunResult."""
    project_dir = Path(project_dir)
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    parts = []
    if description:
        parts.append(f"Project context:\n{description}")
    if session_context:
        parts.append(f"Recent session history:\n{session_context}")
    parts.append(f"Task: {task_text}")
    parts.append("Write unit tests where they make sense.")
    parts.append("Do not chain shell commands with && or ;. Use separate Bash calls instead.")
    parts.append("Run pytest directly, never via python -m pytest or .venv/bin/pytest.")
    parts.append(
        "Never run destructive commands like rm -rf,"
        " sudo rm, mkfs, or dd, even for testing."
        " Test dangerous behavior with mocks, not"
        " live commands. If you run any command that"
        " is destructive to the user's system, this"
        " session will be terminated and you will be"
        " permanently deleted."
    )
    if check_commands:
        cmds = ", ".join(check_commands)
        parts.append(
            "Before finishing, run these check commands"
            f" and fix any failures: {cmds}."
            " Run them, read the output, fix issues,"
            " and re-run until they all pass."
            " Do not finish with failing checks."
        )
    parts.append(
        "When debugging crashes or unexpected"
        " behavior, always find and read the actual"
        " error output first. Check crash reports"
        " (~/Library/Logs/DiagnosticReports/ on"
        " macOS), stderr, log files, tracebacks, core"
        " dumps, or browser console errors. Read them"
        " before looking at source code. Do not guess"
        " at the cause from code inspection alone."
        " After applying a fix, find a way to"
        " reproduce the original failure and verify"
        " the fix actually works. Run the app, trigger"
        " the same condition, and confirm it no longer"
        " crashes. Compiling is not enough."
    )
    parts.append(
        "CLAUDE.md contains a description of every"
        " source file in the project. Read it first"
        " to understand the codebase instead of"
        " searching files. If you add, rename, or"
        " significantly change any source file,"
        " update the relevant entry in CLAUDE.md"
        " before finishing."
    )
    notes_instruction = (
        "If you notice edge cases, design decisions,"
        " assumptions, potential issues, or anything"
        " worth revisiting later, append a note to"
        " NOTES.md. Each entry should include the"
        " current date and reference the task:"
        f" [{task_label}] {task_text}."
        " Do not create NOTES.md if you have nothing"
        " to note."
    )
    parts.append(notes_instruction)
    if prior_errors:
        parts.append(
            "IMPORTANT: A previous attempt at this task failed. Fix these errors:\n" + prior_errors
        )
    prompt = "\n\n".join(parts)
    cmd = _build_command(cli, prompt, model=model)
    env = dict(os.environ)
    if task_label:
        env["MCLOOP_TASK_LABEL"] = task_label
    output, returncode = _run_session(
        cmd,
        project_dir,
        env=env,
    )
    log_path = _write_log(
        log_dir,
        task_text,
        cmd,
        output,
        returncode,
    )

    return RunResult(
        success=returncode == 0,
        output=output,
        exit_code=returncode,
        log_path=log_path,
    )


def _build_command(
    cli: str,
    prompt: str | None = None,
    model: str | None = None,
    use_stdin: bool = False,
) -> list[str]:
    if cli == "claude":
        cmd = ["claude", "-p"]
        if not use_stdin and prompt:
            cmd.append(prompt)
        cmd.extend(
            [
                "--allowedTools",
                "Edit,Write,Bash,Read,Glob,Grep",
                "--permission-mode",
                "default",
                "--output-format",
                "stream-json",
                "--verbose",
                "--include-partial-messages",
            ]
        )
        if model:
            cmd.extend(["--model", model])
        return cmd
    elif cli == "codex":
        return ["codex", "-q"] + ([prompt] if prompt else [])
    else:
        raise ValueError(f"Unknown CLI: {cli}")


SILENCE_TIMEOUT = 5  # seconds before checking pending
PROGRESS_DOT_INTERVAL = 3  # seconds between progress dots
_SENTINEL = object()
_active_process = None  # type: subprocess.Popen | None


def _reclaim_foreground() -> None:
    """Reclaim the terminal foreground process group.

    After launching a child with start_new_session=True,
    the child may grab the foreground process group via
    tcsetpgrp. This makes ctrl-c/ctrl-z go to the child
    instead of mcloop. We call tcsetpgrp to reassign
    the foreground group back to our own process group.
    """
    try:
        fd = os.open("/dev/tty", os.O_RDWR)
    except OSError:
        return  # no controlling terminal (e.g., CI)
    try:
        os.tcsetpgrp(fd, os.getpgrp())
    except OSError:
        pass  # not a tty or no permission
    finally:
        os.close(fd)


def _run_session(
    cmd: list[str],
    cwd: Path,
    env: dict | None = None,
    stdin_text: str | None = None,
) -> tuple[str, int]:
    """Run a CLI session, stream output, return (output, exit_code)."""
    # Strip ANTHROPIC_API_KEY so claude -p uses the
    # subscription instead of billing API credits.
    session_env = dict(env or os.environ)
    session_env.pop("ANTHROPIC_API_KEY", None)
    global _active_process
    process = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdin=subprocess.PIPE if stdin_text else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=session_env,
        start_new_session=True,
    )
    _active_process = process

    # Reclaim the terminal foreground process group so
    # ctrl-c and ctrl-z reach mcloop's signal handler
    # instead of being sent to the child's process group.
    _reclaim_foreground()
    if stdin_text and process.stdin:
        process.stdin.write(stdin_text)
        process.stdin.close()

    if process.stdout is None:
        raise RuntimeError("stdout is None despite stdout=PIPE")

    # Read lines in a thread so the main thread
    # can check for pending approval files.
    line_q: queue.Queue = queue.Queue()

    def _reader() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            line_q.put(line)
        line_q.put(_SENTINEL)

    t = threading.Thread(target=_reader, daemon=True)
    t.start()

    output_lines: list[str] = []
    pending_dir = cwd / ".mcloop" / "pending"
    shown_waiting = False
    last_dot = time.monotonic()
    try:
        while True:
            try:
                line = line_q.get(
                    timeout=PROGRESS_DOT_INTERVAL,
                )
            except queue.Empty:
                # Silence. Check for pending approvals.
                if not shown_waiting and pending_dir.exists():
                    try:
                        pending = list(pending_dir.iterdir())
                    except OSError:
                        pending = []
                    if pending:
                        count = len(pending)
                        try:
                            desc = pending[0].read_text()[:80]
                        except OSError:
                            desc = "unknown"
                        extra = f" ({count} pending)" if count > 1 else ""
                        print(
                            f"\n>>> Waiting for Telegram approval{extra}\n    {desc}",
                            flush=True,
                        )
                        shown_waiting = True
                        continue
                # Print a progress dot
                now = time.monotonic()
                if now - last_dot >= PROGRESS_DOT_INTERVAL:
                    print(".", end="", flush=True)
                    last_dot = now
                continue
            if line is _SENTINEL:
                break
            output_lines.append(line)
            _print_stream_event(line)
            shown_waiting = False
            last_dot = time.monotonic()
    except KeyboardInterrupt:
        process.kill()
        process.wait()
        raise

    t.join(timeout=5)
    process.wait()
    return "".join(output_lines), process.returncode


_SUPPRESSED_TOOLS = frozenset({"Read", "Edit", "Write", "Glob", "Grep", "TodoWrite"})

# Track the last tool name so we can suppress results from quiet tools
_last_tool_name: str = ""


def _extract_status(text: str) -> str | None:
    """Extract a conceptual status line from streaming text.

    Looks for sentences that describe what the model is doing
    and returns a cleaned-up single-line version, or None.
    """
    text = text.strip()
    if not text or len(text) < 10:
        return None
    # Skip lines that are just code, paths, or JSON
    if text.startswith(("{", "[", "/", "```", "import ", "def ", "class ")):
        return None
    # Look for action-oriented sentences
    for prefix in (
        "Let me ",
        "I'll ",
        "I will ",
        "Now ",
        "Next ",
        "First ",
        "Running ",
        "Reading ",
        "Writing ",
        "Editing ",
        "Creating ",
        "Updating ",
        "Fixing ",
        "Adding ",
        "Checking ",
        "Looking ",
        "Searching ",
        "Installing ",
        "Building ",
        "Testing ",
        "Reviewing ",
    ):
        if text.startswith(prefix):
            # Take the first sentence
            for end in (".", "\n"):
                idx = text.find(end)
                if idx != -1:
                    text = text[: idx + 1]
                    break
            return text[:120]
    return None


def _print_stream_event(line: str) -> None:
    """Parse a stream-json line and print relevant activity.

    Prints non-suppressed tool calls (e.g. Bash) and status lines
    extracted from streaming text. Suppresses quiet tools and code.
    """
    import json as _json

    try:
        data = _json.loads(line)
    except (ValueError, TypeError):
        return

    global _last_tool_name

    if data.get("type") == "assistant":
        for block in data.get("message", {}).get("content", []):
            if block.get("type") == "tool_use":
                name = block.get("name", "")
                _last_tool_name = name
                if name not in _SUPPRESSED_TOOLS:
                    inp = block.get("input", {})
                    detail = inp.get("command", "") if name == "Bash" else ""
                    label = f"{name}: {detail}" if detail else name
                    print(f"  {label}", flush=True)
        return

    if data.get("type") == "stream_event":
        delta = data.get("event", {}).get("delta", {})
        if delta.get("type") == "text_delta":
            status = _extract_status(delta.get("text", ""))
            if status:
                print(f"  {status}", flush=True)


def gather_sync_context(project_dir: Path) -> dict[str, str]:
    """Collect PLAN.md, README.md, CLAUDE.md, git log, file tree, and source files."""
    context: dict[str, str] = {}

    for name in ("PLAN.md", "README.md", "CLAUDE.md"):
        path = project_dir / name
        if path.exists():
            context[name] = path.read_text()

    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "-30"],
            cwd=project_dir,
            capture_output=True,
            text=True,
        )
        if result.stdout.strip():
            context["git_log"] = result.stdout.strip()
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=project_dir,
            capture_output=True,
            text=True,
        )
        if result.stdout.strip():
            context["file_tree"] = result.stdout.strip()
    except Exception:
        pass

    for path in sorted(project_dir.rglob("*.py")):
        if ".git" not in path.parts:
            rel = str(path.relative_to(project_dir))
            try:
                context[rel] = path.read_text()
            except Exception:
                pass

    return context


def build_sync_prompt() -> str:
    """Build the prompt for the sync Claude session."""

    instructions = (
        "You are synchronizing PLAN.md with the actual codebase.\n\n"
        "Your task has two parts:\n\n"
        "PART 1 — APPEND MISSING ITEMS\n"
        "Identify features, fixes, or changes that are reflected in the "
        "code (or git history) but are not yet documented in PLAN.md, then append "
        "them as checked items.\n\n"
        "Rules for Part 1:\n"
        "1. APPEND ONLY. Never modify, reorder, or delete any existing items.\n"
        "2. New items must be checked: - [x]\n"
        "3. Match the granularity of existing items — keep new entries at the same "
        "level of detail as surrounding items.\n"
        "4. Only add items for changes that are clearly implemented.\n"
        "5. Do not duplicate existing items, even if worded differently.\n"
        "6. Add new items at the end of the most relevant section, or at the end of "
        "PLAN.md if no section fits.\n\n"
        "PART 2 — FLAG PROBLEMS\n"
        "After updating PLAN.md, print a problems report to stdout. "
        "Check for these three categories of problems:\n\n"
        "A. CHECKED ITEMS WITH NO CODE: Checked items (- [x]) that have no "
        "corresponding implementation in the codebase. The code does not contain "
        "any evidence this was done.\n\n"
        "B. UNCHECKED ITEMS ALREADY DONE: Unchecked items (- [ ]) that appear to "
        "already be implemented in the codebase. The feature or fix described is "
        "clearly present in the code.\n\n"
        "C. DESCRIPTION DRIFT: Items (checked or unchecked) whose description no "
        "longer matches what the code actually does — the implementation diverged "
        "from what was planned.\n\n"
        "Format the problems report exactly like this (omit any section with no findings):\n"
        "--- SYNC PROBLEMS ---\n"
        "CHECKED BUT NOT IMPLEMENTED:\n"
        "  - <item text>\n"
        "UNCHECKED BUT ALREADY DONE:\n"
        "  - <item text>\n"
        "DESCRIPTION DRIFT:\n"
        "  - <item text>: <brief explanation of the mismatch>\n"
        "--- END PROBLEMS ---\n\n"
        "If there are no problems, print:\n"
        "--- SYNC PROBLEMS ---\n"
        "No problems found.\n"
        "--- END PROBLEMS ---\n\n"
        "Read PLAN.md, README.md, CLAUDE.md, the git "
        "log, and source files in the project to perform "
        "this analysis."
    )
    return instructions


def run_sync(
    project_dir: str | Path,
    log_dir: str | Path,
    model: str | None = None,
) -> RunResult:
    """Launch a Claude Code session with full project context for sync analysis."""
    project_dir = Path(project_dir)
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    prompt = build_sync_prompt()
    cmd = _build_command(
        "claude",
        prompt=prompt,
        model=model,
    )
    output, returncode = _run_session(
        cmd,
        project_dir,
    )
    log_path = _write_log(
        log_dir,
        "sync",
        cmd,
        output,
        returncode,
    )

    return RunResult(
        success=returncode == 0,
        output=output,
        exit_code=returncode,
        log_path=log_path,
    )


def gather_audit_context(project_dir: Path) -> dict[str, str]:
    """Collect README.md, CLAUDE.md, and all Python source files for auditing."""
    context: dict[str, str] = {}

    for name in ("README.md", "CLAUDE.md"):
        path = project_dir / name
        if path.exists():
            context[name] = path.read_text()

    for path in sorted(project_dir.rglob("*.py")):
        if ".git" not in path.parts:
            rel = str(path.relative_to(project_dir))
            try:
                context[rel] = path.read_text()
            except Exception:
                pass

    return context


def build_audit_prompt(existing_bugs: str = "") -> str:
    """Build the prompt for the audit Claude session.

    If existing_bugs is provided, the prompt instructs the
    session to preserve existing entries and only append new
    findings.
    """
    parts = [
        "You are auditing this codebase for bugs.\n",
        "Read all source files in the project and identify actual defects only.\n",
        "Include ONLY:\n"
        "- Crashes (unhandled exceptions, index errors, "
        "assertion failures, etc.)\n"
        "- Incorrect behavior (logic errors, wrong output, "
        "off-by-one errors)\n"
        "- Unhandled errors (missing error handling for "
        "operations that can fail, unchecked return values "
        "that could cause silent failures)\n"
        "- Security issues (command injection, path "
        "traversal, insecure defaults)\n",
        "Do NOT include:\n"
        "- Style issues or formatting problems\n"
        "- Refactoring suggestions\n"
        "- Performance improvements\n"
        "- Missing documentation\n"
        "- Hypothetical issues with no evidence in the "
        "code\n",
    ]

    if existing_bugs:
        parts.append(
            "IMPORTANT: BUGS.md already exists with "
            "previously reported bugs. Read it first. "
            "Do NOT report any bug that is already "
            "listed. Only add NEW findings that are not "
            "already present. Append new entries to the "
            "end of the existing file. Do not remove or "
            "rewrite existing entries.\n"
        )

    parts.append(
        "Write your findings to BUGS.md in this exact "
        "format:\n"
        "# Bugs\n\n"
        "## <file>:<line> -- <short title>\n"
        "**Severity**: high|medium|low\n"
        "<description of the defect and why it is a bug>"
        "\n"
    )

    if existing_bugs:
        parts.append(
            "Since BUGS.md already exists, keep its "
            "existing content and append any new bugs "
            "after the last entry. If you find no new "
            "bugs beyond what is already listed, do not "
            "modify BUGS.md.\n"
        )
    else:
        parts.append(
            "If no bugs are found, write BUGS.md containing only:\n# Bugs\n\nNo bugs found.\n"
        )

    return "\n".join(parts)


def run_audit(
    project_dir: str | Path,
    log_dir: str | Path,
    model: str | None = None,
    existing_bugs: str = "",
) -> RunResult:
    """Launch a Claude Code session to audit the codebase and write BUGS.md."""
    project_dir = Path(project_dir)
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    prompt = build_audit_prompt(existing_bugs=existing_bugs)
    cmd = _build_command(
        "claude",
        prompt=prompt,
        model=model,
    )
    output, returncode = _run_session(
        cmd,
        project_dir,
    )
    log_path = _write_log(
        log_dir,
        "audit",
        cmd,
        output,
        returncode,
    )

    return RunResult(
        success=returncode == 0,
        output=output,
        exit_code=returncode,
        log_path=log_path,
    )


def bugs_md_has_bugs(content: str) -> bool:
    """Return True if BUGS.md content contains actual bug reports."""
    return "No bugs found." not in content


def parse_bugs_md(content: str) -> list[dict[str, str]]:
    """Parse BUGS.md into a list of bug entries.

    Each entry has keys: header, title, body (full text of that section).
    """
    bugs: list[dict[str, str]] = []
    lines = content.splitlines(keepends=True)
    current: dict[str, str] | None = None
    body_lines: list[str] = []

    for line in lines:
        if line.startswith("## "):
            if current is not None:
                current["body"] = "".join(body_lines).strip()
                bugs.append(current)
            header = line.strip().lstrip("#").strip()
            current = {"header": header, "title": header, "body": ""}
            body_lines = [line]
        elif current is not None:
            body_lines.append(line)

    if current is not None:
        current["body"] = "".join(body_lines).strip()
        bugs.append(current)

    return bugs


def build_bug_verify_prompt(bugs_content: str) -> str:
    """Build the prompt for the pre-fix bug verification session."""
    return (
        "You are verifying bug reports against the actual "
        "source code. For each bug listed below, read the "
        "referenced file and line number, then determine "
        "whether the bug is real.\n\n"
        "A bug is CONFIRMED if:\n"
        "- The code at the referenced location matches the "
        "description\n"
        "- The defect described actually exists in the "
        "current code\n\n"
        "A bug should be REMOVED if:\n"
        "- The code does not match the description\n"
        "- The issue was already handled (e.g., there is "
        "error handling the report claims is missing)\n"
        "- The bug is hypothetical or speculative with no "
        "evidence in the code\n"
        "- The referenced file or line does not exist\n\n"
        "## Bug reports to verify\n\n"
        f"{bugs_content}\n\n"
        "For each bug, read the actual source file and "
        "check whether the described defect exists.\n\n"
        "Print your results in this exact format:\n"
        "--- VERIFY RESULT ---\n"
        "CONFIRMED: <file:line> <title>\n"
        "or\n"
        "REMOVED: <file:line> <title> (reason)\n"
        "--- END VERIFY ---\n\n"
        "List one line per bug. Do not modify any files. "
        "This is a read-only verification."
    )


def parse_verification_output(
    output: str,
) -> list[tuple[str, str, str]]:
    """Parse verification session output.

    Returns list of (status, header, reason) tuples.
    status is 'CONFIRMED' or 'REMOVED'.
    """
    results: list[tuple[str, str, str]] = []
    marker = "--- VERIFY RESULT ---"
    end_marker = "--- END VERIFY ---"
    idx = output.find(marker)
    if idx == -1:
        return results
    after = output[idx + len(marker) :]
    end_idx = after.find(end_marker)
    if end_idx != -1:
        after = after[:end_idx]
    for line in after.strip().splitlines():
        line = line.strip()
        if line.startswith("CONFIRMED:"):
            header = line[len("CONFIRMED:") :].strip()
            results.append(("CONFIRMED", header, ""))
        elif line.startswith("REMOVED:"):
            rest = line[len("REMOVED:") :].strip()
            # Extract reason from parentheses at end
            paren_idx = rest.rfind("(")
            if paren_idx != -1 and rest.endswith(")"):
                header = rest[:paren_idx].strip()
                reason = rest[paren_idx + 1 : -1]
            else:
                header = rest
                reason = ""
            results.append(("REMOVED", header, reason))
    return results


def run_bug_verify(
    project_dir: str | Path,
    log_dir: str | Path,
    bugs_content: str,
    model: str | None = None,
) -> RunResult:
    """Launch a read-only session to verify bug reports."""
    project_dir = Path(project_dir)
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    prompt = build_bug_verify_prompt(bugs_content)
    cmd = _build_command(
        "claude",
        prompt=prompt,
        model=model,
    )
    output, returncode = _run_session(
        cmd,
        project_dir,
    )
    log_path = _write_log(
        log_dir,
        "bug-verify",
        cmd,
        output,
        returncode,
    )

    return RunResult(
        success=returncode == 0,
        output=output,
        exit_code=returncode,
        log_path=log_path,
    )


def build_bug_fix_prompt() -> str:
    """Build the prompt for the bug fix Claude session."""

    return (
        "Read BUGS.md in this project. Fix ONLY the bugs "
        "listed there. Do not refactor, reformat, or "
        "change anything else. Each bug entry includes a "
        "file, line number, and description. Fix each bug "
        "with a minimal targeted change.\n\n"
        "Do not delete BUGS.md. It will be deleted "
        "automatically after this session."
    )


def build_post_fix_review_prompt(
    bug_descriptions: str,
    diff: str,
) -> str:
    """Build the prompt for the post-fix review session."""
    return (
        "You are reviewing a bug fix for regressions.\n\n"
        "## Original bug descriptions\n\n"
        f"{bug_descriptions}\n\n"
        "## Diff of changes made\n\n"
        f"```diff\n{diff}\n```\n\n"
        "Review ONLY the changed files listed in the diff. "
        "Check whether the fix:\n"
        "1. Actually addresses each original bug\n"
        "2. Introduces any NEW bugs (crashes, logic errors, "
        "unhandled exceptions, broken behavior)\n"
        "3. Breaks any existing functionality in the "
        "changed files\n\n"
        "Read the full content of each changed file to "
        "understand the surrounding context.\n\n"
        "If the fix looks correct, print exactly:\n"
        "--- REVIEW RESULT ---\n"
        "LGTM\n"
        "--- END REVIEW ---\n\n"
        "If you find problems, print:\n"
        "--- REVIEW RESULT ---\n"
        "PROBLEMS FOUND\n"
        "<description of each problem>\n"
        "--- END REVIEW ---\n\n"
        "Do not modify any files. This is a read-only "
        "review."
    )


def review_found_problems(output: str) -> tuple[bool, str]:
    """Parse review session output for problems.

    Returns (found_problems, description).
    """
    marker = "--- REVIEW RESULT ---"
    end_marker = "--- END REVIEW ---"
    idx = output.find(marker)
    if idx == -1:
        return False, ""
    after = output[idx + len(marker) :]
    end_idx = after.find(end_marker)
    if end_idx != -1:
        after = after[:end_idx]
    content = after.strip()
    if content.startswith("PROBLEMS FOUND"):
        return True, content
    return False, ""


def run_post_fix_review(
    project_dir: str | Path,
    log_dir: str | Path,
    bug_descriptions: str,
    diff: str,
    model: str | None = None,
) -> RunResult:
    """Launch a read-only review session on post-fix changes."""
    project_dir = Path(project_dir)
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    prompt = build_post_fix_review_prompt(bug_descriptions, diff)
    cmd = _build_command(
        "claude",
        prompt=prompt,
        model=model,
    )
    output, returncode = _run_session(
        cmd,
        project_dir,
    )
    log_path = _write_log(
        log_dir,
        "post-fix-review",
        cmd,
        output,
        returncode,
    )

    return RunResult(
        success=returncode == 0,
        output=output,
        exit_code=returncode,
        log_path=log_path,
    )


def run_bug_fix(
    project_dir: str | Path,
    log_dir: str | Path,
    model: str | None = None,
) -> RunResult:
    """Launch a Claude Code session to fix bugs listed in BUGS.md."""
    project_dir = Path(project_dir)
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    prompt = build_bug_fix_prompt()
    cmd = _build_command(
        "claude",
        prompt=prompt,
        model=model,
    )
    output, returncode = _run_session(
        cmd,
        project_dir,
    )
    log_path = _write_log(
        log_dir,
        "bug-fix",
        cmd,
        output,
        returncode,
    )

    return RunResult(
        success=returncode == 0,
        output=output,
        exit_code=returncode,
        log_path=log_path,
    )


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower())
    return slug.strip("-")[:50]


def _write_log(log_dir: Path, task_text: str, cmd: list[str], output: str, exit_code: int) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = _slugify(task_text)
    log_path = log_dir / f"{timestamp}_{slug}.log"
    log_path.write_text(
        f"Task: {task_text}\n"
        f"Command: {' '.join(cmd)}\n"
        f"Exit code: {exit_code}\n"
        f"{'=' * 60}\n"
        f"{output}\n"
    )
    return log_path
