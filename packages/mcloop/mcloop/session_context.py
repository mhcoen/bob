"""Rolling session context shared between task sessions."""

from __future__ import annotations

MAX_FLAT_CONTEXT_ENTRIES = 5


class SessionContext:
    """Rolling context shared between task sessions within a run.

    Resets when moving to a new top-level task group.
    For flat tasks (no subtasks), keeps the last N entries.
    """

    def __init__(self) -> None:
        self._entries: list[str] = []
        self._current_group: str = ""

    def update_group(self, label: str, has_subtasks: bool) -> None:
        """Reset context if we moved to a new top-level group."""
        group = label.split(".")[0]
        if group != self._current_group:
            self._entries.clear()
            self._current_group = group
        if not has_subtasks:
            # Flat tasks: trim to last N
            if len(self._entries) > MAX_FLAT_CONTEXT_ENTRIES:
                self._entries = self._entries[-MAX_FLAT_CONTEXT_ENTRIES:]

    def add(
        self,
        label: str,
        task_text: str,
        elapsed: str,
        output: str,
        changed_files: list[str] | None = None,
    ) -> None:
        """Append a brief summary of a completed task."""
        # Extract the last few meaningful lines
        lines = output.strip().splitlines()
        summary_lines = []
        for line in reversed(lines):
            stripped = line.strip()
            if not stripped:
                continue
            # Skip JSON blobs from stream output
            if stripped.startswith("{"):
                continue
            summary_lines.append(stripped)
            if len(summary_lines) >= 3:
                break
        summary_lines.reverse()
        summary = "; ".join(summary_lines)[:200]
        entry = f"[{label}] {task_text} ({elapsed})"
        if summary:
            entry += f": {summary}"
        if changed_files:
            entry += f"\n  Files: {', '.join(changed_files)}"
        self._entries.append(entry)

    def add_user_input(self, text: str) -> None:
        """Append free-form user input to context."""
        self._entries.append(f"[user] {text}")

    def text(self) -> str:
        """Return context string for inclusion in prompts."""
        return "\n".join(self._entries)
