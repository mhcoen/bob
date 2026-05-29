"""Run AI queries through the claude CLI instead of direct API calls."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from duplo import call_log

_DOT_INTERVAL_SECONDS = 5.0
_POLL_INTERVAL_SECONDS = 0.5
_TIMEOUT_SECONDS = 600
_MAX_ATTEMPTS = 3
_RETRY_SLEEP_SECONDS = 5.0

# Flags that switch ``claude -p`` to a parseable, usage-bearing stream.
# ``--include-partial-messages`` surfaces the raw ``message_start`` /
# ``message_delta`` events that carry per-turn token counts.
_STREAM_JSON_FLAGS = [
    "--output-format",
    "stream-json",
    "--verbose",
    "--include-partial-messages",
]

# The four token-usage fields duplo records for quota analysis.
_USAGE_KEYS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
)


_T = TypeVar("_T")


class ClaudeCliError(Exception):
    """Raised when the claude CLI returns a non-zero exit code."""


def _parse_stream_json(stdout: str) -> tuple[str, dict[str, int] | None]:
    """Parse ``claude --output-format stream-json`` output.

    Returns ``(response_text, usage)``. ``usage`` maps the four
    :data:`_USAGE_KEYS` token counts, summed across turns:
    ``input_tokens`` / ``cache_*_input_tokens`` come from ``message_start``
    events and ``output_tokens`` from ``message_delta`` events. The
    response text is taken from the terminal ``result`` event when present,
    otherwise reconstructed from streamed ``text_delta`` chunks.

    If the output is not parseable stream-json at all (no JSON object on any
    line), the raw text is returned with ``usage`` of ``None`` so the call
    still succeeds rather than failing on a format surprise. ``usage`` is
    also ``None`` when JSON parsed but carried no token counts.
    """
    usage = {key: 0 for key in _USAGE_KEYS}
    saw_usage = False
    text_parts: list[str] = []
    result_text: str | None = None
    parsed_any = False

    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(obj, dict):
            continue
        parsed_any = True

        event = obj.get("event") if obj.get("type") == "stream_event" else obj
        if not isinstance(event, dict):
            event = obj
        etype = event.get("type")

        if etype == "message_start":
            msg = event.get("message")
            u = msg.get("usage", {}) if isinstance(msg, dict) else {}
            for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
                value = u.get(key)
                if isinstance(value, int):
                    usage[key] += value
                    saw_usage = True
        elif etype == "message_delta":
            value = event.get("usage", {}).get("output_tokens")
            if isinstance(value, int):
                usage["output_tokens"] += value
                saw_usage = True
        elif etype == "content_block_delta":
            delta = event.get("delta", {})
            if isinstance(delta, dict) and delta.get("type") == "text_delta":
                text_parts.append(delta.get("text", ""))

        if obj.get("type") == "result" and isinstance(obj.get("result"), str):
            result_text = obj["result"]

    if not parsed_any:
        return stdout.strip(), None
    response = result_text if result_text is not None else "".join(text_parts)
    return response.strip(), (usage if saw_usage else None)


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


def _with_retry(func: Callable[..., _T], *args: Any, **kwargs: Any) -> tuple[_T, int]:
    """Call ``func`` with up to ``_MAX_ATTEMPTS`` attempts on ClaudeCliError.

    Sleeps ``_RETRY_SLEEP_SECONDS`` between attempts and prints a progress
    message to stderr before each retry. Returns ``(result, attempt)`` where
    ``attempt`` is the 1-based number of the attempt that succeeded.
    Re-raises the last ClaudeCliError if every attempt fails.
    """
    last_err: ClaudeCliError | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return func(*args, **kwargs), attempt
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


def _classify_error(err: ClaudeCliError) -> str:
    """Map a ClaudeCliError to a ``call_log`` outcome: ``"timeout"`` or ``"error"``."""
    return "timeout" if "timed out" in str(err) else "error"


def query(prompt: str, *, system: str = "", model: str = "sonnet", call_site: str = "") -> str:
    """Send a text prompt to ``claude -p`` and return the response text.

    Runs the CLI via ``subprocess.Popen`` and prints a dot to stderr every
    ``_DOT_INTERVAL_SECONDS`` while the call is in flight so the user sees
    progress during long-running generations. A trailing newline is printed
    once the call completes. On failure (timeout or non-zero exit) the call
    is retried up to ``_MAX_ATTEMPTS`` times. Every call appends one
    ``call_log`` record, on success and on failure alike.

    Args:
        prompt: The user prompt to send.
        system: Optional system prompt.
        model: Model alias or full name (default ``"sonnet"``).
        call_site: Label identifying the phase/feature/step that invoked
            this call; recorded in the ``call_log`` record.

    Returns:
        The response text stripped of leading/trailing whitespace.

    Raises:
        ClaudeCliError: If every attempt exits with a non-zero code or times out.
    """
    start = time.perf_counter()
    try:
        (response, usage), attempt = _with_retry(_query_once, prompt, system=system, model=model)
    except ClaudeCliError as err:
        call_log.log_call(
            provider="claude_cli",
            call_site=call_site,
            model=model,
            prompt=prompt,
            system=system,
            error=str(err),
            outcome=_classify_error(err),
            attempt=_MAX_ATTEMPTS,
            duration_seconds=time.perf_counter() - start,
        )
        raise
    call_log.log_call(
        provider="claude_cli",
        call_site=call_site,
        model=model,
        prompt=prompt,
        system=system,
        response=response,
        outcome="ok",
        attempt=attempt,
        duration_seconds=time.perf_counter() - start,
        usage=usage,
    )
    return response


def _query_once(prompt: str, *, system: str, model: str) -> tuple[str, dict[str, int] | None]:
    cmd = ["claude", "-p", "--model", model, *_STREAM_JSON_FLAGS]
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
    return _parse_stream_json("".join(stdout_parts))


def query_with_images(
    prompt: str,
    image_paths: list[Path],
    *,
    system: str = "",
    model: str = "sonnet",
    call_site: str = "",
) -> str:
    """Send a prompt with image file references to ``claude -p``.

    Instructs Claude to read each image file using the Read tool,
    then respond based on the system prompt. On failure (timeout or
    non-zero exit) the call is retried up to ``_MAX_ATTEMPTS`` times.
    Every call appends one ``call_log`` record, on success and on
    failure alike.

    Args:
        prompt: The analysis instructions.
        image_paths: Paths to image files for Claude to read.
        system: Optional system prompt.
        model: Model alias or full name (default ``"sonnet"").
        call_site: Label identifying the phase/feature/step that invoked
            this call; recorded in the ``call_log`` record.

    Returns:
        The response text stripped of leading/trailing whitespace.

    Raises:
        ClaudeCliError: If every attempt exits with a non-zero code or times out.
    """
    start = time.perf_counter()
    try:
        (response, usage), attempt = _with_retry(
            _query_with_images_once, prompt, image_paths, system=system, model=model
        )
    except ClaudeCliError as err:
        call_log.log_call(
            provider="claude_cli",
            call_site=call_site,
            model=model,
            prompt=prompt,
            system=system,
            error=str(err),
            outcome=_classify_error(err),
            attempt=_MAX_ATTEMPTS,
            duration_seconds=time.perf_counter() - start,
            extra={"image_paths": [str(p) for p in image_paths]},
        )
        raise
    call_log.log_call(
        provider="claude_cli",
        call_site=call_site,
        model=model,
        prompt=prompt,
        system=system,
        response=response,
        outcome="ok",
        attempt=attempt,
        duration_seconds=time.perf_counter() - start,
        usage=usage,
        extra={"image_paths": [str(p) for p in image_paths]},
    )
    return response


def _query_with_images_once(
    prompt: str,
    image_paths: list[Path],
    *,
    system: str,
    model: str,
) -> tuple[str, dict[str, int] | None]:
    image_lines = [f"- {Path(path).resolve()}" for path in image_paths]
    full_prompt = (
        "Read the following image files using the Read tool, "
        "then analyze them as instructed.\n\n"
        "Image files:\n" + "\n".join(image_lines) + "\n\n" + prompt
    )
    cmd = ["claude", "-p", "--model", model, "--tools", "Read", *_STREAM_JSON_FLAGS]
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
    return _parse_stream_json(result.stdout)
