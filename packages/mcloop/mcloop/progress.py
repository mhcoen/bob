"""Periodic activity reports for direct CLI sessions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field


def _brief(value: object) -> str:
    if not isinstance(value, str):
        return ""
    value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
    value = " ".join(value.split())
    value = "".join(char for char in value if char.isprintable())
    return value if len(value) <= 160 else value[:157] + "..."


def _activity(line: str) -> tuple[str, str] | None:
    try:
        event = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(event, dict):
        return None
    kind = event.get("type")
    if kind in ("item.started", "item.updated", "item.completed"):
        item = event.get("item")
        if not isinstance(item, dict):
            return None
        item_kind = item.get("type")
        state = item.get("status")
        finished = kind == "item.completed"
        if item_kind == "command_execution":
            command = _brief(item.get("command"))
            exit_code = item.get("exit_code")
            if finished:
                outcome = f"exit {exit_code}" if type(exit_code) is int else _brief(state)
                summary = f"Command completed ({outcome or 'exit unknown'}): {command}"
            else:
                summary = f"Command running: {command}"
        elif item_kind == "file_change":
            changes = item.get("changes")
            paths = (
                [
                    _brief(change.get("path"))
                    for change in changes or []
                    if isinstance(change, dict)
                ]
                if isinstance(changes, list)
                else []
            )
            summary = f"File changes ({_brief(state) or 'reported'}): {_brief(', '.join(paths))}"
        elif item_kind == "mcp_tool_call":
            tool = _brief(item.get("tool"))
            summary = f"Tool {tool}: {_brief(state) or ('completed' if finished else 'running')}"
        elif item_kind == "web_search":
            summary = f"Web search: {_brief(item.get('query'))}"
        elif item_kind == "agent_message":
            summary = f"Agent message: {_brief(item.get('text'))}"
        else:
            # Reasoning content and raw tool output are not terminal status.
            return None
        return (str(item.get("id")) + summary, summary)
    if kind in ("error", "turn.failed"):
        error = event.get("error")
        message = error.get("message") if isinstance(error, dict) else event.get("message")
        summary = f"CLI error: {_brief(message) or 'no details supplied'}"
        return (summary, summary)
    blocks = []
    if kind == "assistant":
        message = event.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), list):
            blocks = message["content"]
    elif kind == "stream_event":
        stream = event.get("event")
        if isinstance(stream, dict) and stream.get("type") == "content_block_start":
            blocks = [stream.get("content_block")]
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = _brief(block.get("name"))
        inputs = block.get("input")
        detail = ""
        if isinstance(inputs, dict):
            for key in ("command", "file_path", "path", "pattern", "query", "url"):
                detail = _brief(inputs.get(key))
                if detail:
                    break
        summary = f"Tool requested: {name} {detail}".rstrip()
        return (str(block.get("id")) + summary, summary)
    return None


@dataclass
class SessionProgress:
    label: str
    started: float
    interval: float = 30.0
    last_output: float | None = None
    last_activity: float | None = None
    activity: str = ""
    _activity_key: str = ""
    _last_report: float = field(init=False)

    def __post_init__(self) -> None:
        self._last_report = self.started

    def observe(self, line: str, now: float) -> None:
        self.last_output = now
        action = _activity(line)
        if action is not None and action[0] != self._activity_key:
            self._activity_key, self.activity = action
            self.last_activity = now

    def report(self, now: float, *, waiting: str = "") -> str:
        if now - self._last_report < self.interval:
            return ""
        self._last_report = now
        output_age = (
            "no output received"
            if self.last_output is None
            else f"last output {now - self.last_output:.0f}s ago"
        )
        lines = [f"[{self.label}] {now - self.started:.0f}s elapsed; {output_age}"]
        if waiting:
            lines.append(f"    Waiting for Telegram approval: {_brief(waiting)}")
        if self.last_activity is not None:
            lines.append(
                f"    Last activity ({now - self.last_activity:.0f}s ago): {self.activity}"
            )
        elif not waiting:
            lines.append("    No tool activity reported yet.")
        return "\n".join(lines)
