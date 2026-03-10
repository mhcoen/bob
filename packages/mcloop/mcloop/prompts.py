"""Prompt builders and output parsers for AI CLI sessions."""

from __future__ import annotations

from mcloop.investigator import (
    DEBUGGING_INSTRUCTION,
    DEBUGGING_PLAYBOOK,
    PROBES_INSTRUCTION,
    TESTING_INSTRUCTION,
    WEB_SEARCH_INSTRUCTION,
)


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
        "PART 2 — CHECK OFF COMPLETED ITEMS AND FLAG PROBLEMS\n"
        "Scan every unchecked item (- [ ]) in PLAN.md. If the feature "
        "or fix it describes is clearly implemented in the codebase, "
        "change it to checked (- [x]). Do NOT uncheck any item.\n\n"
        "Then print a problems report to stdout. "
        "Check for these two categories of problems:\n\n"
        "A. CHECKED ITEMS WITH NO CODE: Checked items (- [x]) that have no "
        "corresponding implementation in the codebase. The code does not contain "
        "any evidence this was done.\n\n"
        "B. DESCRIPTION DRIFT: Items (checked or unchecked) whose description no "
        "longer matches what the code actually does — the implementation diverged "
        "from what was planned.\n\n"
        "Format the problems report exactly like this (omit any section with no findings):\n"
        "--- SYNC PROBLEMS ---\n"
        "CHECKED BUT NOT IMPLEMENTED:\n"
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
        "IMPORTANT: This is a source-code-only review. "
        "Read the source files and reason about defects "
        "from the code. Do NOT run bash commands, python "
        "snippets, or any other experiments to test edge "
        "cases. Do NOT execute the code. Only use the "
        "Read tool to examine source files. Report only "
        "bugs you can see directly in the code.\n",
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
        "NO_PROBLEMS\n"
        "--- END REVIEW ---\n\n"
        "If you find problems, print:\n"
        "--- REVIEW RESULT ---\n"
        "PROBLEMS FOUND\n"
        "<description of each problem>\n"
        "--- END REVIEW ---\n\n"
        "Do not modify any files. This is a read-only "
        "review."
    )


def build_investigation_plan_description(
    bug_context: str,
    failure_history: str = "",
) -> str:
    """Build the description for an investigation PLAN.md.

    This description is prepended to generated investigation plans
    so that every investigation session enforces structured note-taking.
    """
    parts = [
        "You are investigating a bug. Follow the debugging playbook:\n" + DEBUGGING_PLAYBOOK,
    ]
    parts.append(PROBES_INSTRUCTION)
    parts.append(WEB_SEARCH_INSTRUCTION)
    parts.append(TESTING_INSTRUCTION)
    parts.append(DEBUGGING_INSTRUCTION)
    if bug_context:
        parts.append(f"Bug context:\n{bug_context}")
    if failure_history:
        parts.append(f"## What has been tried\n\n{failure_history}")
    else:
        parts.append("## What has been tried\n\nNothing yet.")
    parts.append(
        "NOTES.md must use three sections:"
        " ## Observations (confirmed facts from"
        " runtime, docs, logs, or experiments),"
        " ## Hypotheses (candidate explanations not"
        " yet confirmed), and ## Eliminated (things"
        " ruled out, with the experiment that ruled"
        " them out). Place each note under the"
        " appropriate section."
    )
    parts.append(
        "Before proposing any approach, read the"
        " ## Eliminated section of NOTES.md. Do not"
        " repeat an eliminated approach unless you"
        " have new evidence that contradicts the"
        " original elimination. If you find yourself"
        " about to try something already eliminated,"
        " stop and explain what new evidence would"
        " justify revisiting it."
    )
    return "\n\n".join(parts)


def build_diagnostic_prompt(
    error_entry: dict,
    source_content: str,
    git_log: str,
) -> str:
    """Build prompt for a diagnostic session that analyzes a crash.

    The session reads the crash context and relevant source code,
    then produces a one-line fix description suitable for a PLAN.md
    task.
    """
    parts = [
        "You are diagnosing a crash. Analyze the error context"
        " and source code below, then produce a one-line fix"
        " description.\n",
    ]

    # Error context
    exc_type = error_entry.get("exception_type", "Unknown")
    desc = error_entry.get("description", "")
    source_file = error_entry.get("source_file", "")
    line = error_entry.get("line", "")
    stack = error_entry.get("stack_trace", "")
    app_state = error_entry.get("app_state", {})
    last_action = error_entry.get("last_action", "")

    parts.append(f"Exception type: {exc_type}")
    parts.append(f"Description: {desc}")
    if source_file:
        loc = f"{source_file}:{line}" if line else source_file
        parts.append(f"Location: {loc}")
    if stack:
        parts.append(f"Stack trace:\n{stack}")
    if app_state:
        state_lines = "\n".join(f"  {k}: {v}" for k, v in app_state.items())
        parts.append(f"App state at crash:\n{state_lines}")
    if last_action:
        parts.append(f"Last user action: {last_action}")

    if source_content:
        parts.append(f"Relevant source file:\n```\n{source_content}\n```")

    if git_log:
        parts.append(f"Recent git log:\n{git_log}")

    parts.append(
        "\nPrint your fix description in this exact format:\n"
        "--- FIX DESCRIPTION ---\n"
        "<one-line description of what to fix and how>\n"
        "--- END FIX ---\n\n"
        "The description should be actionable and specific,"
        " suitable as a task in a checklist. Example:\n"
        "--- FIX DESCRIPTION ---\n"
        "Guard against None return from parse_config() in"
        " main.py:42 by adding a None check before accessing"
        " .value\n"
        "--- END FIX ---\n\n"
        "Do not modify any files. This is a read-only"
        " diagnostic session."
    )
    return "\n\n".join(parts)


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
    # Accept both NO_PROBLEMS and legacy LGTM
    return False, ""


def parse_diagnostic_output(output: str) -> str:
    """Extract fix description from diagnostic session output.

    Returns the fix description string, or empty string if not
    found.
    """
    marker = "--- FIX DESCRIPTION ---"
    end_marker = "--- END FIX ---"
    idx = output.find(marker)
    if idx == -1:
        return ""
    after = output[idx + len(marker) :]
    end_idx = after.find(end_marker)
    if end_idx != -1:
        after = after[:end_idx]
    return after.strip()


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


def bugs_md_has_bugs(content: str) -> bool:
    """Return True if BUGS.md content contains actual bug reports."""
    return "No bugs found." not in content
