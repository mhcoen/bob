"""Public CLI activity summaries shared by McLoop and Orchestra."""

import json
import re


def brief(value: object) -> str:
    if not isinstance(value, str):
        return ""
    value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
    value = " ".join(value.split())
    value = "".join(char for char in value if char.isprintable())
    return value if len(value) <= 160 else value[:157] + "..."


def activity(line: str) -> tuple[str, str] | None:
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
            command = brief(item.get("command"))
            exit_code = item.get("exit_code")
            if finished:
                outcome = f"exit {exit_code}" if type(exit_code) is int else brief(state)
                summary = f"Command completed ({outcome or 'exit unknown'}): {command}"
            else:
                summary = f"Command running: {command}"
        elif item_kind == "file_change":
            changes = item.get("changes")
            paths = (
                [brief(change.get("path")) for change in changes or [] if isinstance(change, dict)]
                if isinstance(changes, list)
                else []
            )
            summary = f"File changes ({brief(state) or 'reported'}): {brief(', '.join(paths))}"
        elif item_kind == "mcp_tool_call":
            tool = brief(item.get("tool"))
            summary = f"Tool {tool}: {brief(state) or ('completed' if finished else 'running')}"
        elif item_kind == "web_search":
            summary = f"Web search: {brief(item.get('query'))}"
        elif item_kind == "agent_message":
            summary = f"Agent message: {brief(item.get('text'))}"
        else:
            # Reasoning content and raw tool output are not terminal status.
            return None
        return (str(item.get("id")) + summary, summary)
    if kind in ("error", "turn.failed"):
        error = event.get("error")
        message = error.get("message") if isinstance(error, dict) else event.get("message")
        summary = f"CLI error: {brief(message) or 'no details supplied'}"
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
        name = brief(block.get("name"))
        inputs = block.get("input")
        detail = ""
        if isinstance(inputs, dict):
            for key in ("command", "file_path", "path", "pattern", "query", "url"):
                detail = brief(inputs.get(key))
                if detail:
                    break
        summary = f"Tool requested: {name} {detail}".rstrip()
        return (str(block.get("id")) + summary, summary)
    return None
