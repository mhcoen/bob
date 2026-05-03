"""Per-state progress reporting for the verb-style CLI and the REPL.

The executor surfaces ``state_enter`` and ``state_exit`` events to a
``ProgressCallback`` if one is supplied via
``run_workflow(progress_callback=...)``. The CLI and REPL install a
default reporter that prints one line to stderr per event:

    [1/7] framer (claude_code_text:opus) ... starting
    [1/7] framer (claude_code_text:opus) ... done in 3.2s

Both lines are appended (the "done" line does not overwrite the
"starting" line) so adapter-side progress dots and other interleaved
output do not corrupt the cursor position. ``--quiet`` on the CLI
suppresses the reporter without changing the executor contract: the
callback is still optional, and omitting it is the same as passing one
that does nothing.

Stdout is reserved for the workflow's final answer so piping
``orchestra ask "..." | something`` keeps working.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import IO


@dataclass(frozen=True)
class ProgressEvent:
    """One progress notification surfaced by the executor.

    ``kind`` is ``"state_enter"`` or ``"state_exit"``. ``index`` is the
    1-based ordinal of the state's first entry (retries reuse the
    same index). ``total`` is the count of declared states in the
    workflow. ``adapter`` and ``model`` come from the resolved actor
    binding. ``elapsed_seconds`` is ``None`` for ``state_enter`` and
    the wall-clock duration for ``state_exit``.
    """

    kind: str
    state_name: str
    role: str | None
    adapter: str | None
    model: str | None
    index: int
    total: int
    elapsed_seconds: float | None


ProgressCallback = Callable[[ProgressEvent], None]
"""Signature the executor calls per state_enter and state_exit."""


def format_event(event: ProgressEvent) -> str:
    """Render one ``ProgressEvent`` as a single line.

    Format mirrors the example in the CLI section of the README:

        [N/M] role (adapter:model) ... starting
        [N/M] role (adapter:model) ... done in 3.2s

    ``role`` falls back to the state name when no role is bound (some
    transform states have no role). ``adapter:model`` collapses to
    just ``adapter`` when no model is configured (e.g. the anonymize
    transform). The ``...`` separator is always present so visual
    columns line up across long verb lists.
    """
    role_label = event.role or event.state_name
    if event.adapter and event.model:
        backing = f"{event.adapter}:{event.model}"
    elif event.adapter:
        backing = event.adapter
    elif event.model:
        backing = event.model
    else:
        backing = "transform" if event.kind in ("state_enter", "state_exit") else "?"

    counter = f"[{event.index}/{event.total}]"
    head = f"{counter} {role_label} ({backing}) ..."
    if event.kind == "state_enter":
        return f"{head} starting"
    if event.kind == "state_exit":
        if event.elapsed_seconds is None:
            return f"{head} done"
        return f"{head} done in {event.elapsed_seconds:.1f}s"
    return f"{head} {event.kind}"


def stderr_reporter(stream: IO[str] | None = None) -> ProgressCallback:
    """Return a progress callback that prints each event to ``stream``.

    Defaults to ``sys.stderr``. The callback flushes after every line
    so a long-running state's "starting" line shows up immediately
    rather than buffering until the run finishes.
    """
    target = stream if stream is not None else sys.stderr

    def _print(event: ProgressEvent) -> None:
        print(format_event(event), file=target, flush=True)

    return _print


def silent_reporter() -> ProgressCallback:
    """Return a callback that drops every event.

    Equivalent to passing ``progress_callback=None`` from the
    executor's perspective. Useful when a caller wants to pass an
    explicit "off" rather than threading None through several layers.
    """

    def _drop(_event: ProgressEvent) -> None:
        return None

    return _drop
