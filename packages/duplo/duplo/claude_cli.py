"""Run AI queries through the claude CLI instead of direct API calls."""

from __future__ import annotations

import subprocess
from pathlib import Path


class ClaudeCliError(Exception):
    """Raised when the claude CLI returns a non-zero exit code."""


def query(prompt: str, *, system: str = "", model: str = "haiku") -> str:
    """Send a text prompt to ``claude -p`` and return the response text.

    Args:
        prompt: The user prompt to send.
        system: Optional system prompt.
        model: Model alias or full name (default ``"haiku"``).

    Returns:
        The response text stripped of leading/trailing whitespace.

    Raises:
        ClaudeCliError: If the CLI exits with a non-zero code.
    """
    cmd = ["claude", "-p", "--model", model]
    if system:
        cmd.extend(["--system-prompt", system])
    result = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ClaudeCliError(f"claude exited with code {result.returncode}: {result.stderr}")
    return result.stdout.strip()


def query_with_images(
    prompt: str,
    image_paths: list[Path],
    *,
    system: str = "",
    model: str = "haiku",
) -> str:
    """Send a prompt with image file references to ``claude -p``.

    Instructs Claude to read each image file using the Read tool,
    then respond based on the system prompt.

    Args:
        prompt: The analysis instructions.
        image_paths: Paths to image files for Claude to read.
        system: Optional system prompt.
        model: Model alias or full name (default ``"haiku"``).

    Returns:
        The response text stripped of leading/trailing whitespace.

    Raises:
        ClaudeCliError: If the CLI exits with a non-zero code.
    """
    image_lines = [f"- {path}" for path in image_paths]
    full_prompt = (
        "Read the following image files using the Read tool, "
        "then analyze them as instructed.\n\n"
        "Image files:\n" + "\n".join(image_lines) + "\n\n" + prompt
    )
    cmd = ["claude", "-p", "--model", model, "--tools", "Read"]
    if system:
        cmd.extend(["--system-prompt", system])
    result = subprocess.run(
        cmd,
        input=full_prompt,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ClaudeCliError(f"claude exited with code {result.returncode}: {result.stderr}")
    return result.stdout.strip()
