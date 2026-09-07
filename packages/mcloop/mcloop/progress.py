"""Periodic activity reports for direct CLI sessions."""

from __future__ import annotations

from dataclasses import dataclass, field

from orchestra.adapters.activity import activity as _activity
from orchestra.adapters.activity import brief as _brief


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
