"""Collect user feedback and known issues as text input or from a file."""

from __future__ import annotations

from pathlib import Path
from typing import Callable


def collect_feedback(
    feedback_file: Path | str | None = None,
    *,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
) -> str:
    """Return user feedback as a string.

    If *feedback_file* is given, read the feedback from that path.
    Otherwise, prompt the user to type feedback interactively (finish with
    an empty line or EOF).

    Args:
        feedback_file: Optional path to a plain-text feedback file.
        input_fn: Callable used to read user input (default ``input``).
        print_fn: Callable used to print prompts (default ``print``).

    Returns:
        Feedback text, stripped of leading/trailing whitespace.

    Raises:
        FileNotFoundError: If *feedback_file* is given but does not exist.
        ValueError: If no feedback was provided (empty input or empty file).
    """
    if feedback_file is not None:
        text = Path(feedback_file).read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError(f"Feedback file is empty: {feedback_file}")
        return text

    return _read_interactive(input_fn=input_fn, print_fn=print_fn)


def _read_interactive(
    *,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
) -> str:
    """Prompt the user to type multi-line feedback, ending with a blank line."""
    print_fn("Enter feedback (blank line or Ctrl-D to finish):")
    lines: list[str] = []
    try:
        while True:
            line = input_fn("")
            if line == "":
                break
            lines.append(line)
    except EOFError:
        pass

    text = "\n".join(lines).strip()
    if not text:
        raise ValueError("No feedback provided.")
    return text


def collect_issues(
    *,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
) -> list[str]:
    """Prompt the user for known issues with the completed phase.

    Each issue may span multiple lines.  A blank line finishes the
    current issue and prompts for the next one.  An immediate blank
    line (or EOF) ends input entirely.  Returns an empty list if the
    user enters nothing (no issues to report is a valid response).

    Args:
        input_fn: Callable used to read user input (default ``input``).
        print_fn: Callable used to print prompts (default ``print``).

    Returns:
        List of issue description strings (may be empty).
    """
    print_fn("Any known issues with this phase?")
    print_fn('  e.g. bugs ("waveform shows static bars during recording")')
    print_fn('       incomplete wiring ("qwen3-asr-swift dependency is unused")')
    print_fn("  Blank line to finish each issue, blank line to stop:")
    issues: list[str] = []
    lines: list[str] = []
    try:
        while True:
            lines = []
            while True:
                line = input_fn("")
                if line == "":
                    break
                lines.append(line)
            text = "\n".join(lines).strip()
            if not text:
                break
            issues.append(text)
    except EOFError:
        text = "\n".join(lines).strip()
        if text:
            issues.append(text)
    return issues
