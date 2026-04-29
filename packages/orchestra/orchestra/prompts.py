"""Prompt builders lifted from mcloop's runner.

Mcloop builds three different task prompts depending on the call site:
``_build_normal_prompt`` for a fresh task, ``_build_bug_task_prompt``
for a task that came from BUGS.md, and ``_build_bug_prompt`` when the
previous attempt left ``prior_errors``. All three call into a shared
``_build_shared_parts`` that injects safety rules, check-command rules,
the NOTES.md guidance, and the wrap-marker preservation rule.

The api computes the right variant from the same nine inputs mcloop's
``run_task`` receives, then injects the result into the workflow run as
a synthetic external input named ``final_prompt``. The packaged
templates substitute ``{final_prompt}`` directly. This keeps the
prompt construction faithful (no format_map conditionals) while still
threading the inputs through the slice 1 grammar.

The transcription is structural: same prompt parts in the same order
with the same wording. Anything that depends on mcloop's local config
(``rtk`` detection, ``MCLOOP_TASK_LABEL`` env var) stays out because
it is the wrapper's job, not the prompt's.
"""

from __future__ import annotations

from typing import Any


def _build_shared_parts(
    task_text: str,
    task_label: str,
    check_commands: list[str] | None,
) -> list[str]:
    """Return prompt parts shared by all variants.

    Lifted from mcloop ``_build_shared_parts``.
    """
    parts: list[str] = []
    parts.append(
        "Do not chain shell commands with && or ;. Use separate Bash calls instead."
    )
    parts.append(
        "Never set, unset, or override environment variables"
        " in Bash commands. Do not use VAR=value command,"
        " env -u, unset, or export. The environment is"
        " controlled by the orchestrator."
    )
    parts.append(
        "Never run destructive commands like rm -rf,"
        " sudo rm, mkfs, or dd, even for testing."
        " Test dangerous behavior with mocks, not"
        " live commands. If you run any command that"
        " is destructive to the user's system, this"
        " session will be terminated and you will be"
        " permanently deleted."
    )
    parts.append(
        "Never delete any file. Do not use rm, git rm,"
        " os.remove, unlink, shutil.rmtree, or any"
        " other file deletion mechanism. Do not delete"
        " PLAN.md, CLAUDE.md, NOTES.md, or any other"
        " project file under any circumstances. If you"
        " believe a file should be removed, leave it"
        " and note it in NOTES.md for the user to"
        " decide."
    )
    if check_commands:
        cmds = ", ".join(check_commands)
        parts.append(
            "CHECK COMMANDS (mandatory, strict rules):\n"
            f"Commands: {cmds}\n"
            "1. Run each check command EXACTLY ONCE before finishing.\n"
            "2. Run the command exactly as listed. Do not append"
            " | tail, | head, or any pipe. Do not truncate output."
            " Do not modify the command in any way.\n"
            "3. If a check fails, fix the issue, then re-run that"
            " same exact check command ONCE.\n"
            "4. Maximum 3 total runs of any single check command."
            " If it still fails after 3 runs, stop and report"
            " what is failing.\n"
            "5. NEVER run the same check command twice in a row"
            " without making a code change between runs."
            " Re-running a passing test is forbidden.\n"
            "6. ONLY run the exact check commands listed above."
            " Do not run subsets, individual test files, or"
            " any variation. Do not run pytest on a single file."
            " Do not run any test command other than the ones"
            " listed here. The orchestrator runs its own"
            " verification after this session ends."
        )
    parts.append(
        "Do not remove or modify code between"
        " mcloop:wrap markers (e.g. `// mcloop:wrap:begin`"
        " ... `// mcloop:wrap:end` or the Python `#`"
        " equivalents). These are auto-injected crash"
        " handlers managed by mcloop. If a task requires"
        " changes to the entry point file, work around"
        " the marked block."
    )
    parts.append(
        "If you notice edge cases, design decisions,"
        " assumptions, potential issues, or anything"
        " worth revisiting later, append a note to"
        " NOTES.md. Each entry should include the"
        " current date and reference the task:"
        f" [{task_label}] {task_text}."
        " Do not create NOTES.md if you have nothing"
        " to note."
        " NOTES.md must use three sections:"
        " ## Observations (confirmed facts from"
        " runtime, docs, logs, or experiments),"
        " ## Hypotheses (candidate explanations not"
        " yet confirmed), and ## Eliminated (things"
        " ruled out, with the experiment that ruled"
        " them out). Place each note under the"
        " appropriate section."
    )
    parts.append(
        "When building UI (SwiftUI, HTML, React, Qt,"
        " or any other UI framework), add accessibility"
        " identifiers to every interactive element"
        " (buttons, text fields, menu items, toggles,"
        " sliders, pickers, links, tabs). Use the"
        " platform-native API: .accessibilityIdentifier()"
        " in SwiftUI, data-testid in HTML/React,"
        " setAccessibleName() in Qt. This makes the"
        " app programmatically testable."
    )
    parts.append(
        "Never install tools or dependencies via brew,"
        " cargo, pip, npm, apt, or any other package"
        " manager. If a required tool is not found,"
        " report what is missing and stop. Do not"
        " search for alternative ways to obtain it."
        " The user will install it and re-run."
    )
    return parts


def _ruled_out_section(eliminated: list[str] | None) -> str | None:
    if not eliminated:
        return None
    return (
        "RULED OUT APPROACHES: The following approaches"
        " have already been tried for this task and"
        " failed. Do not repeat any of them. If you"
        " find yourself heading toward a ruled out"
        " approach, stop and try a fundamentally"
        " different strategy.\n" + "\n".join(eliminated)
    )


def build_normal_prompt(
    task_text: str,
    description: str,
    task_label: str,
    session_context: str,
    check_commands: list[str] | None,
    eliminated: list[str] | None = None,
) -> str:
    """Prompt for a fresh task. Lifted from mcloop ``_build_normal_prompt``."""
    parts: list[str] = []
    if description:
        parts.append(f"Project context:\n{description}")
    if session_context:
        parts.append(f"Recent session history:\n{session_context}")
    parts.append(f"Task: {task_text}")
    parts.append(
        "Write unit tests only when the change introduces"
        " non-obvious behavior or a regression risk. Trivial"
        " additions (constants, dataclass fields, simple"
        " delegations) do not need tests."
    )
    parts.append(
        "Tests must NEVER make real subprocess calls to"
        " claude, codex, or any LLM CLI. Any function"
        " that transitively invokes an LLM must be mocked."
        " Before writing a test that calls a function,"
        " check its source to see if it reaches an LLM"
        " call path. If it does, mock at the narrowest"
        " point that eliminates the real call. Real LLM"
        " round-trips cost 5-15 seconds each and will"
        " make the test suite unusably slow."
    )
    parts.extend(_build_shared_parts(task_text, task_label, check_commands))
    ruled = _ruled_out_section(eliminated)
    if ruled:
        parts.append(ruled)
    return "\n\n".join(parts)


def build_bug_task_prompt(
    task_text: str,
    description: str,
    task_label: str,
    session_context: str,
    check_commands: list[str] | None,
    eliminated: list[str] | None = None,
) -> str:
    """First-attempt bug-task prompt. Lifted from mcloop
    ``_build_bug_task_prompt``."""
    parts: list[str] = []
    if description:
        parts.append(f"Project context:\n{description}")
    parts.append(
        "BUG FIX (MANDATORY CODE CHANGE): This task comes"
        " from BUGS.md. The behavior described below is"
        " confirmed broken. You MUST modify code to fix it."
        " Do not exit without making changes. If a function"
        " mentioned in the task already exists, that does not"
        " mean the bug is fixed -- read the task carefully,"
        " it describes what the function should do differently"
        " from what it does now. Exiting without file changes"
        " means you failed."
    )
    if session_context:
        parts.append(f"Recent session history:\n{session_context}")
    parts.append(f"Task: {task_text}")
    parts.append(
        "Fix the bug with a targeted change. Write or update"
        " tests to cover the new behavior so it cannot regress."
    )
    parts.extend(_build_shared_parts(task_text, task_label, check_commands))
    ruled = _ruled_out_section(eliminated)
    if ruled:
        parts.append(ruled)
    return "\n\n".join(parts)


def build_bug_prompt(
    task_text: str,
    description: str,
    task_label: str,
    session_context: str,
    check_commands: list[str] | None,
    prior_errors: str,
    eliminated: list[str] | None,
) -> str:
    """Bug-investigation prompt with prior errors. Lifted from mcloop
    ``_build_bug_prompt``."""
    parts: list[str] = []
    if description:
        parts.append(f"Project context:\n{description}")
    parts.append(
        "BUG INVESTIGATION: A previous attempt at this task"
        " failed. Your primary goal is to diagnose and fix"
        " the errors below. Read the error output carefully"
        " before reading source code. Understand the root"
        " cause before changing anything."
    )
    parts.append(f"ERRORS FROM PREVIOUS ATTEMPT:\n{prior_errors}")
    if session_context:
        parts.append(f"Recent session history:\n{session_context}")
    parts.append(f"Task: {task_text}")
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
        "Fix the bug with a minimal, targeted change."
        " Do not refactor surrounding code. Write or"
        " update tests to cover the failure case so"
        " it cannot regress."
    )
    parts.extend(_build_shared_parts(task_text, task_label, check_commands))
    ruled = _ruled_out_section(eliminated)
    if ruled:
        parts.append(ruled)
    return "\n\n".join(parts)


def build_code_edit_prompt(inputs: dict[str, Any]) -> str:
    """Build the final code-edit prompt from the run_workflow inputs.

    Selects ``build_bug_prompt`` if ``prior_errors`` is non-empty (the
    retry path), ``build_bug_task_prompt`` if ``is_bug_task`` is set
    (a task pulled from BUGS.md), or ``build_normal_prompt`` otherwise.
    Mirrors the branching mcloop's ``run_task`` does at the call site
    so a wrapper that calls run_workflow gets the same prompt mcloop
    would have produced.
    """
    instruction = str(inputs.get("instruction", ""))
    description = str(inputs.get("description", ""))
    task_label = str(inputs.get("task_label", ""))
    session_context = str(inputs.get("context", ""))
    prior_errors = str(inputs.get("prior_errors", ""))
    is_bug_task = bool(inputs.get("is_bug_task", False))
    check_commands_raw = inputs.get("check_commands")
    check_commands = (
        [str(c) for c in check_commands_raw]
        if isinstance(check_commands_raw, list)
        else None
    )
    eliminated_raw = inputs.get("eliminated")
    eliminated = (
        [str(e) for e in eliminated_raw]
        if isinstance(eliminated_raw, list)
        else None
    )

    if prior_errors:
        return build_bug_prompt(
            instruction,
            description,
            task_label,
            session_context,
            check_commands,
            prior_errors,
            eliminated,
        )
    if is_bug_task:
        return build_bug_task_prompt(
            instruction,
            description,
            task_label,
            session_context,
            check_commands,
            eliminated,
        )
    return build_normal_prompt(
        instruction,
        description,
        task_label,
        session_context,
        check_commands,
        eliminated,
    )
