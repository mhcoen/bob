"""Rate limit detection and CLI fallover."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

RATE_LIMIT_PATTERNS = [
    "rate limit",
    "rate_limit_error",
    "rate_limit_exceeded",
    "too many requests",
    "usage limit",
    "quota exceeded",
    "over capacity",
    "5-hour message limit",
]

# Substrings in stream JSON metadata that contain "rate_limit"
# but do not indicate an actual rate limit error. These lines
# are stripped before pattern matching.
_STREAM_METADATA_MARKERS = [
    "rate_limit_event",
    "rate_limit_info",
    "rateLimitType",
]

# Patterns checked via regex (word-boundary matching to avoid false positives)
_RATE_LIMIT_REGEX_PATTERNS = [
    r"\b429\b",
]

SESSION_LIMIT_PATTERNS = [
    "credit balance is too low",
    "session limit",
    "billing_error",
    "exceeded your plan",
    "usage cap",
    "you've hit your limit",
    "hit your limit",
    "5-hour message limit",
    "weekly limit",
]

DEFAULT_COOLDOWN = 300  # 5 minutes
SESSION_LIMIT_POLL = 600  # 10 minutes


@dataclass
class RateLimitState:
    limited: dict[str, float] = field(default_factory=dict)  # cli -> reset timestamp

    def mark_limited(self, cli: str, cooldown: int = DEFAULT_COOLDOWN) -> None:
        self.limited[cli] = time.time() + cooldown

    def is_limited(self, cli: str) -> bool:
        reset_at = self.limited.get(cli)
        if reset_at is None:
            return False
        if time.time() >= reset_at:
            del self.limited[cli]
            return False
        return True

    def seconds_until_reset(self) -> float | None:
        now = time.time()
        active = [t for t in self.limited.values() if t > now]
        if not active:
            return None
        return min(active) - now


def _strip_metadata_lines(text: str) -> str:
    """Remove stream JSON lines that contain rate_limit metadata.

    Claude Code's stream-json output includes rate_limit_event
    objects in every session regardless of whether a rate limit
    occurred. These must be excluded before pattern matching.
    """
    lines = text.splitlines()
    return "\n".join(
        line for line in lines if not any(marker in line for marker in _STREAM_METADATA_MARKERS)
    )


def is_rate_limited(output: str, exit_code: int) -> bool:
    """Detect rate limiting from CLI output."""
    if exit_code == 0:
        return False
    cleaned = _strip_metadata_lines(output).lower()
    if any(p in cleaned for p in RATE_LIMIT_PATTERNS):
        return True
    return any(re.search(p, cleaned) for p in _RATE_LIMIT_REGEX_PATTERNS)


def is_session_limited(output: str, exit_code: int) -> bool:
    """Detect session/billing limit from CLI output."""
    if exit_code == 0:
        return False
    cleaned = _strip_metadata_lines(output).lower()
    return any(p in cleaned for p in SESSION_LIMIT_PATTERNS)


ALL_CLIS = ("claude", "codex")


def get_available_cli(
    state: RateLimitState,
    preferred: str = "claude",
    enabled_clis: tuple[str, ...] = ALL_CLIS,
) -> str | None:
    """Return an available CLI name, or None if all are limited."""
    # Try preferred first, then others in order
    candidates = [preferred] + [c for c in enabled_clis if c != preferred]
    for cli in candidates:
        if cli in enabled_clis and not state.is_limited(cli):
            return cli
    return None


def wait_for_reset(
    state: RateLimitState,
    notify_fn=None,
    enabled_clis: tuple[str, ...] = ALL_CLIS,
) -> str:
    """Block until a CLI becomes available. Returns the CLI name."""
    if notify_fn:
        secs = state.seconds_until_reset()
        notify_fn(f"All CLIs rate-limited. Pausing ~{int(secs or 0)}s.", level="warning")

    while True:
        cli = get_available_cli(state, enabled_clis=enabled_clis)
        if cli:
            if notify_fn:
                notify_fn(f"Resuming with {cli}.", level="info")
            return cli
        time.sleep(10)


# ---------------------------------------------------------------------------
# Shared sub-session limit handling (T-000027)
#
# The main task loop in main.py already inspects each session's OUTPUT for a
# rate/session limit and waits/falls-over. The audit, bug-verify, bug-fix,
# post-fix-review and diagnostic sub-sessions historically did not: they
# branched only on RunResult.success/.exit_code and treated a 429/session
# limit as an ordinary failure (e.g. "audit: session exited with code 1,
# skipping fix" -> terminal "Audit failed"). The helpers below give those
# paths the same detection + bounded wait-for-reset the task loop uses, and
# surface limit events to STDOUT (not just Telegram).
#
# Reach: the five sub-sessions hardcode the claude CLI and there is no
# per-sub-session model chain to tell us codex is installed and appropriate,
# so the callers pass enabled_clis=("claude",). Blindly spawning codex with a
# claude-oriented prompt would risk turning a transient claude limit into a
# hard spawn failure. The helper itself is CLI-agnostic, so enabling codex
# fallover later is a one-argument change once a per-sub-session chain exists.
# ---------------------------------------------------------------------------

# Bounded retry budget for a limited sub-session before it is deferred as
# inconclusive. A genuine multi-hour limit will not clear inside this window;
# the point is to wait out a transient throttle, then get out of the way
# rather than block phase completion on a quota event.
LIMIT_MAX_ATTEMPTS = 3

SessionStatus = Literal["ok", "failed", "deferred"]


class _SessionResultLike(Protocol):
    success: bool
    output: str
    exit_code: int


def classify_session_result(output: str, exit_code: int, success: bool) -> Literal["ok", "limited", "failed"]:
    """Classify a finished sub-session.

    ``ok`` -> the session succeeded. ``limited`` -> it failed AND the output
    carries a rate-limit or session/billing-limit marker. ``failed`` -> a
    genuine (non-limit) failure that must surface as before.
    """
    if success:
        return "ok"
    if is_session_limited(output, exit_code) or is_rate_limited(output, exit_code):
        return "limited"
    return "failed"


@dataclass
class SessionOutcome:
    """Result of running a sub-session under limit-aware fallover."""

    result: _SessionResultLike
    status: SessionStatus
    cli: str


def run_session_with_fallover(
    run_fn: Callable[[str], _SessionResultLike],
    *,
    state: RateLimitState,
    context: str,
    notify_fn: Callable[..., None] | None = None,
    echo_fn: Callable[[str], None] | None = None,
    preferred_cli: str = "claude",
    enabled_clis: tuple[str, ...] = ("claude",),
    max_attempts: int = LIMIT_MAX_ATTEMPTS,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> SessionOutcome:
    """Run ``run_fn(cli)`` with the task loop's rate-limit handling.

    ``run_fn`` performs one sub-session on the given CLI and returns a
    RunResult-like object (``.success``, ``.output``, ``.exit_code``).

    On a detected rate/session limit the active CLI is marked limited, the
    event is surfaced to STDOUT and ``notify_fn`` (Telegram), and the helper
    waits for reset / falls over to another enabled CLI and retries, up to
    ``max_attempts``. If the budget is exhausted the outcome is ``deferred``
    (inconclusive) rather than ``failed`` so a quota event never aborts the
    run. A genuine non-limit failure returns ``failed`` immediately, matching
    prior behavior.

    ``sleep_fn`` is injectable so tests do not actually sleep.
    """

    def _surface(message: str, level: str = "warning") -> None:
        line = f"[mcloop] {message}"
        if echo_fn is not None:
            echo_fn(line)
        else:
            print(line, flush=True)
        if notify_fn is not None:
            notify_fn(message, level=level)

    cli = get_available_cli(state, preferred=preferred_cli, enabled_clis=enabled_clis) or preferred_cli
    attempt = 0
    while True:
        attempt += 1
        result = run_fn(cli)
        verdict = classify_session_result(result.output, result.exit_code, result.success)
        if verdict == "ok":
            return SessionOutcome(result=result, status="ok", cli=cli)
        if verdict == "failed":
            return SessionOutcome(result=result, status="failed", cli=cli)

        # verdict == "limited"
        state.mark_limited(cli, cooldown=SESSION_LIMIT_POLL)
        _surface(f"{context}: {cli} rate/session-limited (attempt {attempt}/{max_attempts}).")
        if attempt >= max_attempts:
            _surface(
                f"{context}: still rate-limited after {attempt} attempt(s); "
                "deferring as inconclusive (not a failure). Phase completion is not blocked.",
                level="warning",
            )
            return SessionOutcome(result=result, status="deferred", cli=cli)

        nxt = get_available_cli(state, preferred=preferred_cli, enabled_clis=enabled_clis)
        if nxt is None:
            secs = state.seconds_until_reset() or 0
            _surface(f"{context}: all CLIs limited; waiting ~{int(secs)}s for reset.")
            sleep_fn(secs if secs > 0 else 1)
            nxt = get_available_cli(state, preferred=preferred_cli, enabled_clis=enabled_clis) or preferred_cli
        elif nxt != cli:
            _surface(f"{context}: falling over to {nxt}.", level="info")
        cli = nxt
