#!/usr/bin/env python3
"""Stub CLI that simulates an AI coding assistant for integration tests.

Reads a prompt from argv, consults a scenario file (JSON) to determine:
- What files to create or modify
- What output to print (stdout)
- What exit code to return
- How long to wait before responding

Usage:
    python stub_cli.py --scenario SCENARIO_FILE -p PROMPT [--output-format stream-json]

Scenario file format:
    {
      "tasks": [
        {
          "match": "regex pattern to match against the prompt",
          "files": {
            "path/to/file.py": "file contents to write",
            "path/to/existing.py": {"patch": "content to append"}
          },
          "output": "text to print to stdout",
          "exit_code": 0,
          "delay": 0.5
        }
      ],
      "default": {
        "output": "No matching scenario found",
        "exit_code": 1,
        "delay": 0
      }
    }

The first task whose "match" regex matches the prompt is used.
If no task matches, the "default" block is used (or exit 1 with
a generic message if no default is provided).
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path


def _find_scenario(tasks: list[dict], prompt: str) -> dict | None:
    """Return the first task whose match pattern hits the prompt."""
    for task in tasks:
        pattern = task.get("match", "")
        if re.search(pattern, prompt, re.IGNORECASE):
            return task
    return None


def _apply_files(files: dict[str, str | dict]) -> None:
    """Create or modify files as specified."""
    for path_str, content in files.items():
        p = Path(path_str)
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, dict):
            # Patch mode: append to existing file
            if content.get("patch"):
                existing = p.read_text() if p.exists() else ""
                p.write_text(existing + content["patch"])
        else:
            # Create/overwrite mode
            p.write_text(content)


def _emit_output(text: str, stream_json: bool) -> None:
    """Print output, optionally in stream-json format."""
    if stream_json:
        for line in text.splitlines():
            event = json.dumps(
                {
                    "type": "assistant",
                    "subtype": "text",
                    "text": line,
                }
            )
            print(event, flush=True)
    else:
        print(text, flush=True)


def main(argv: list[str] | None = None) -> int:
    """Entry point. Parse args, load scenario, execute."""
    args = argv if argv is not None else sys.argv[1:]

    # Parse arguments
    scenario_path = None
    prompt = None
    stream_json = False
    i = 0
    while i < len(args):
        if args[i] == "--scenario":
            i += 1
            scenario_path = args[i]
        elif args[i] == "-p":
            i += 1
            prompt = args[i]
        elif args[i] == "--output-format":
            i += 1
            if args[i] == "stream-json":
                stream_json = True
        i += 1

    if scenario_path is None:
        print("Error: --scenario is required", file=sys.stderr)
        return 2
    if prompt is None:
        print("Error: -p (prompt) is required", file=sys.stderr)
        return 2

    # Load scenario
    try:
        scenario = json.loads(Path(scenario_path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"Error loading scenario: {exc}",
            file=sys.stderr,
        )
        return 2

    tasks = scenario.get("tasks", [])
    default = scenario.get("default", {})

    # Find matching task
    task = _find_scenario(tasks, prompt)
    if task is None:
        task = default

    # Apply delay
    delay = task.get("delay", 0)
    if delay > 0:
        time.sleep(delay)

    # Apply file operations
    files = task.get("files", {})
    if files:
        _apply_files(files)

    # Emit output
    output = task.get("output", "")
    if output:
        _emit_output(output, stream_json)

    return task.get("exit_code", 0)


if __name__ == "__main__":
    sys.exit(main())
