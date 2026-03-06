"""Collect user feedback as text input or from a file."""

from __future__ import annotations

from pathlib import Path


def collect_feedback(feedback_file: Path | str | None = None) -> str:
    """Return user feedback as a string.

    If *feedback_file* is given, read the feedback from that path.
    Otherwise, prompt the user to type feedback interactively (finish with
    an empty line or EOF).

    Args:
        feedback_file: Optional path to a plain-text feedback file.

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

    return _read_interactive()


def _read_interactive() -> str:
    """Prompt the user to type multi-line feedback, ending with a blank line."""
    print("Enter feedback (blank line or Ctrl-D to finish):")
    lines: list[str] = []
    try:
        while True:
            line = input()
            if line == "":
                break
            lines.append(line)
    except EOFError:
        pass

    text = "\n".join(lines).strip()
    if not text:
        raise ValueError("No feedback provided.")
    return text
