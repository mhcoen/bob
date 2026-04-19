"""Run AI queries through the claude CLI instead of direct API calls."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

_DOT_INTERVAL_SECONDS = 5.0
_POLL_INTERVAL_SECONDS = 0.5
_TIMEOUT_SECONDS = 600
_MAX_ATTEMPTS = 3
_RETRY_SLEEP_SECONDS = 5.0


class ClaudeCliError(Exception):
    """Raised when the claude CLI returns a non-zero exit code."""


def _drain_stream(stream, sink: list[str]) -> None:
    """Read chunks from ``stream`` into ``sink`` until EOF."""
    if stream is None:
        return
    try:
        while True:
            chunk = stream.read(4096)
            if not chunk:
                break
            sink.append(chunk)
    except (ValueError, OSError):
        pass


def _with_retry(func, *args, **kwargs):
    """Call ``func`` with up to ``_MAX_ATTEMPTS`` attempts on ClaudeCliError.

    Sleeps ``_RETRY_SLEEP_SECONDS`` between attempts and prints a progress
    message to stderr before each retry. Re-raises the last ClaudeCliError
    if every attempt fails.
    """
    last_err: ClaudeCliError | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return func(*args, **kwargs)
        except ClaudeCliError as err:
            last_err = err
            if attempt < _MAX_ATTEMPTS:
                sys.stderr.write(
                    f"claude CLI attempt {attempt}/{_MAX_ATTEMPTS} timed out, retrying...\n"
                )
                sys.stderr.flush()
                time.sleep(_RETRY_SLEEP_SECONDS)
    assert last_err is not None
    raise last_err


def query(prompt: str, *, system: str = "", model: str = "sonnet") -> str:
    """Send a text prompt to ``claude -p`` and return the response text.

    Runs the CLI via ``subprocess.Popen`` and prints a dot to stderr every
    ``_DOT_INTERVAL_SECONDS`` while the call is in flight so the user sees
    progress during long-running generations. A trailing newline is printed
    once the call completes. On failure (timeout or non-zero exit) the call
    is retried up to ``_MAX_ATTEMPTS`` times.

    Args:
        prompt: The user prompt to send.
        system: Optional system prompt.
        model: Model alias or full name (default ``"sonnet"``).

    Returns:
        The response text stripped of leading/trailing whitespace.

    Raises:
        ClaudeCliError: If every attempt exits with a non-zero code or times out.
    """
    return _with_retry(_query_once, prompt, system=system, model=model)


def _query_once(prompt: str, *, system: str, model: str) -> str:
    cmd = ["claude", "-p", "--model", model]
    if system:
        cmd.extend(["--system-prompt", system])
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    try:
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
    except FileNotFoundError:
        raise ClaudeCliError("claude CLI not found. Install it from https://claude.ai/download")

    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    stdout_thread = threading.Thread(
        target=_drain_stream, args=(process.stdout, stdout_parts), daemon=True
    )
    stderr_thread = threading.Thread(
        target=_drain_stream, args=(process.stderr, stderr_parts), daemon=True
    )
    stdout_thread.start()
    stderr_thread.start()

    if process.stdin is not None:
        try:
            process.stdin.write(prompt)
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass

    start = time.monotonic()
    last_dot = start
    try:
        while process.poll() is None:
            now = time.monotonic()
            if now - start > _TIMEOUT_SECONDS:
                process.kill()
                raise ClaudeCliError(f"claude CLI timed out after {_TIMEOUT_SECONDS} seconds")
            if now - last_dot >= _DOT_INTERVAL_SECONDS:
                sys.stderr.write(".")
                sys.stderr.flush()
                last_dot = now
            time.sleep(_POLL_INTERVAL_SECONDS)
    finally:
        sys.stderr.write("\n")
        sys.stderr.flush()
        stdout_thread.join()
        stderr_thread.join()

    if process.returncode != 0:
        raise ClaudeCliError(
            f"claude exited with code {process.returncode}: {''.join(stderr_parts)}"
        )
    return "".join(stdout_parts).strip()


def query_with_images(
    prompt: str,
    image_paths: list[Path],
    *,
    system: str = "",
    model: str = "sonnet",
) -> str:
    """Send a prompt with image file references to ``claude -p``.

    Instructs Claude to read each image file using the Read tool,
    then respond based on the system prompt. On failure (timeout or
    non-zero exit) the call is retried up to ``_MAX_ATTEMPTS`` times.

    Args:
        prompt: The analysis instructions.
        image_paths: Paths to image files for Claude to read.
        system: Optional system prompt.
        model: Model alias or full name (default ``"sonnet"").

    Returns:
        The response text stripped of leading/trailing whitespace.

    Raises:
        ClaudeCliError: If every attempt exits with a non-zero code or times out.
    """
    return _with_retry(_query_with_images_once, prompt, image_paths, system=system, model=model)


def _query_with_images_once(
    prompt: str,
    image_paths: list[Path],
    *,
    system: str,
    model: str,
) -> str:
    image_lines = [f"- {Path(path).resolve()}" for path in image_paths]
    full_prompt = (
        "Read the following image files using the Read tool, "
        "then analyze them as instructed.\n\n"
        "Image files:\n" + "\n".join(image_lines) + "\n\n" + prompt
    )
    cmd = ["claude", "-p", "--model", model, "--tools", "Read"]
    if system:
        cmd.extend(["--system-prompt", system])
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    try:
        result = subprocess.run(
            cmd,
            input=full_prompt,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            env=env,
        )
    except FileNotFoundError:
        raise ClaudeCliError("claude CLI not found. Install it from https://claude.ai/download")
    except subprocess.TimeoutExpired:
        raise ClaudeCliError(f"claude CLI timed out after {_TIMEOUT_SECONDS} seconds")
    if result.returncode != 0:
        raise ClaudeCliError(f"claude exited with code {result.returncode}: {result.stderr}")
    return result.stdout.strip()
