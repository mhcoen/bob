"""Rate limit detection and CLI fallover."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

RATE_LIMIT_PATTERNS = [
    "rate limit",
    "rate_limit",
    "too many requests",
    "429",
    "usage limit",
    "quota exceeded",
    "capacity",
]

DEFAULT_COOLDOWN = 300  # 5 minutes


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


def is_rate_limited(output: str, exit_code: int) -> bool:
    """Detect rate limiting from CLI output."""
    if exit_code == 0:
        return False
    lower = output.lower()
    return any(p in lower for p in RATE_LIMIT_PATTERNS)


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


def wait_for_reset(state: RateLimitState, notify_fn=None) -> str:
    """Block until a CLI becomes available. Returns the CLI name."""
    if notify_fn:
        secs = state.seconds_until_reset()
        notify_fn(f"All CLIs rate-limited. Pausing ~{int(secs or 0)}s.", level="warning")

    while True:
        cli = get_available_cli(state)
        if cli:
            if notify_fn:
                notify_fn(f"Resuming with {cli}.", level="info")
            return cli
        time.sleep(10)
