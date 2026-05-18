"""Visual formatting for terminal output.

Provides distinct visual styles for:
- User prompts: bold, reversed, impossible to miss
- Auto observations: visible but subordinate to user prompts
- System actions: plain >>> prefix
- Errors: bold !!! prefix
"""

from __future__ import annotations

import os
import re
import sys

_COMMAND_RE = re.compile(r"^\s*\$\s")

# ANSI escape codes
BOLD = "\033[1m"
DIM = "\033[2m"
REVERSE = "\033[7m"
RED = "\033[31m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
CYAN = "\033[36m"
RESET = "\033[0m"


def _use_color() -> bool:
    """Return True if we should use ANSI color codes."""
    if os.environ.get("NO_COLOR"):
        return False
    if not hasattr(sys.stdout, "isatty"):
        return False
    return sys.stdout.isatty()


def _format_user_instructions(instructions: str) -> str:
    """Render multi-line [USER] instructions for the banner. No reflow.

    Each input line is indented two spaces. Lines that look like shell
    commands (start with ``$ ``) are flanked by blank lines so they
    stand out and remain copy-pasteable.
    """
    raw = instructions.split("\n")
    out: list[str] = []
    for idx, line in enumerate(raw):
        is_cmd = bool(_COMMAND_RE.match(line))
        prev_cmd = idx > 0 and bool(_COMMAND_RE.match(raw[idx - 1]))
        if is_cmd and not prev_cmd and out and out[-1] != "":
            out.append("")
        if not is_cmd and prev_cmd and line.strip():
            out.append("")
        out.append(f"  {line}" if line else "")
    return "\n".join(out)


def user_banner(label: str, instructions: str) -> str:
    """Format a [USER] task banner that's impossible to miss."""
    color = _use_color()
    width = 60
    body_text = _format_user_instructions(instructions)
    if color:
        top = f"\n{BOLD}{REVERSE}{YELLOW} {'=' * (width - 2)} {RESET}"
        title = (
            f"{BOLD}{REVERSE}{YELLOW}"
            f"  >>> USER ACTION REQUIRED  (Task {label})"
            f"{' ' * max(0, width - 38 - len(label))}"
            f"{RESET}"
        )
        bot = f"{BOLD}{REVERSE}{YELLOW} {'=' * (width - 2)} {RESET}"
        body = f"\n{BOLD}{body_text}{RESET}\n"
        sep = f"{DIM}{'-' * width}{RESET}"
        prompt_1 = "When done, type what you observed below."
        prompt_2 = "Press Enter on an empty line to finish:"
        return f"{top}\n{title}\n{bot}\n{body}\n{sep}\n{prompt_1}\n{prompt_2}\n{sep}"
    else:
        top = "\n" + "=" * width
        title = f"  >>> USER ACTION REQUIRED  (Task {label})"
        bot = "=" * width
        body = f"\n{body_text}\n"
        sep = "-" * width
        prompt_1 = "When done, type what you observed below."
        prompt_2 = "Press Enter on an empty line to finish:"
        return f"{top}\n{title}\n{bot}\n{body}\n{sep}\n{prompt_1}\n{prompt_2}\n{sep}"


def auto_banner(label: str, action: str, args: str) -> str:
    """Format an [AUTO] task banner."""
    color = _use_color()
    width = 60
    if color:
        top = f"\n{CYAN}{'─' * width}{RESET}"
        title = f"{BOLD}{CYAN}  AUTO OBSERVATION  (Task {label}){RESET}"
        detail = f"  Action: {action}\n  Args: {args}"
        bot = f"{CYAN}{'─' * width}{RESET}"
        return f"{top}\n{title}\n{detail}\n{bot}"
    else:
        top = "\n" + "─" * width
        title = f"  AUTO OBSERVATION  (Task {label})"
        detail = f"  Action: {action}\n  Args: {args}"
        bot = "─" * width
        return f"{top}\n{title}\n{detail}\n{bot}"


def task_header(label: str, text: str, cli: str) -> str:
    """Format a task start header."""
    color = _use_color()
    if color:
        return f"\n{BOLD}>>> Task {label}) {text}{RESET} {DIM}(using {cli}){RESET}"
    else:
        return f"\n>>> Task {label}) {text} (using {cli})"


def format_task_id(task: object) -> str:
    """Return ``"[T-NNNNNN] "`` when the task carries a canonical id, else ``""``.

    Centralizes the R4 = Option B surfacing convention so every user-facing
    site that prints ``task.text`` can prepend the structured id without
    fusing it into the description. The ``None`` branch keeps the call
    safe when the helper is exercised from unit-test fixtures that
    construct Task instances positionally without a task_id; production
    callers (post-R2 parser + the canonical-form precondition gate) only
    feed Tasks where ``task_id`` is populated.
    """
    task_id = getattr(task, "task_id", None)
    return f"[{task_id}] " if task_id else ""


def task_complete(label: str, elapsed: str) -> str:
    """Format a task completion message."""
    color = _use_color()
    if color:
        return f"\n{GREEN}>>> Completed {label}) [{elapsed}]{RESET}"
    else:
        return f"\n>>> Completed {label}) [{elapsed}]"


def error_msg(text: str) -> str:
    """Format an error message."""
    color = _use_color()
    if color:
        return f"\n{BOLD}{RED}!!! {text}{RESET}"
    else:
        return f"\n!!! {text}"


def system_msg(text: str) -> str:
    """Format a system status message."""
    color = _use_color()
    if color:
        return f"\n{DIM}>>> {text}{RESET}"
    else:
        return f"\n>>> {text}"


def summary_header() -> str:
    """Format the summary section header."""
    color = _use_color()
    width = 40
    if color:
        return f"\n{BOLD}{'=' * width}\nMcLoop Summary\n{'=' * width}{RESET}"
    else:
        return f"\n{'=' * width}\nMcLoop Summary\n{'=' * width}"


def summary_footer() -> str:
    """Format the summary section footer."""
    color = _use_color()
    width = 40
    if color:
        return f"{BOLD}{'=' * width}{RESET}"
    else:
        return "=" * width


def strip_code_fences(text: str) -> str:
    """Strip wrapping markdown code fences from LLM responses."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text


def format_elapsed(seconds: float) -> str:
    """Format seconds into human-readable elapsed time."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours = int(minutes // 60)
    mins = minutes % 60
    return f"{hours}h {mins}m {secs}s"
