"""Plan Ledger Slice D: per-run configuration.

Slice D is gated at three layers, in order of precedence (highest
wins):

  1. CLI flags: ``--no-plan-ledger`` and ``--no-auto-reauthor``.
  2. Environment variables: ``MCLOOP_NO_PLAN_LEDGER`` and
     ``MCLOOP_NO_AUTO_REAUTHOR``.
  3. ``.orchestra/config.json`` ``plan_ledger`` section in the
     project directory, if present.

Default behavior when nothing is configured: Plan Ledger emission
is enabled iff ``<project_dir>/.duplo/ledger/`` already exists
(i.e., the project has been initialized for Plan Ledger by Duplo
or by manual setup); auto-reauthor is on. Projects that have not
been initialized for Plan Ledger see no behavior change from
Slice D.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_TRUTHY = ("1", "true", "yes", "on")
_DEFAULT_LEDGER_DIR = ".duplo/ledger"

_ENV_NO_PLAN_LEDGER = "MCLOOP_NO_PLAN_LEDGER"
_ENV_NO_AUTO_REAUTHOR = "MCLOOP_NO_AUTO_REAUTHOR"


@dataclass(frozen=True)
class PlanLedgerSettings:
    """Per-run Plan Ledger configuration."""

    enabled: bool
    auto_reauthor: bool
    ledger_dir: Path
    plan_path: Path
    threshold_params: dict[str, Any] = field(default_factory=dict)


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def _read_orchestra_config(project_dir: Path) -> dict[str, Any]:
    """Read ``<project_dir>/.orchestra/config.json``.

    Returns an empty dict on any error (file absent, malformed, IO
    failure). Slice D's contract is "absent or unreadable config
    means default behavior", not "absent config crashes McLoop".
    """
    config_path = project_dir / ".orchestra" / "config.json"
    if not config_path.is_file():
        return {}
    try:
        with config_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def load_plan_ledger_settings(
    *,
    project_dir: Path,
    plan_path: Path | None = None,
    cli_no_plan_ledger: bool = False,
    cli_no_auto_reauthor: bool = False,
) -> PlanLedgerSettings:
    """Resolve Plan Ledger settings for a McLoop run.

    Parameters
    ----------
    project_dir
        The project the McLoop run is operating against. Used to
        locate ``.orchestra/config.json`` and the default ledger
        directory.
    plan_path
        Path to PLAN.md. Defaults to ``<project_dir>/PLAN.md``.
    cli_no_plan_ledger
        ``--no-plan-ledger`` flag wins over env and config.
    cli_no_auto_reauthor
        ``--no-auto-reauthor`` flag wins over env and config.
    """
    project_dir = Path(project_dir).resolve()
    plan_path = Path(plan_path).resolve() if plan_path is not None else (project_dir / "PLAN.md")

    raw = _read_orchestra_config(project_dir)
    pl = raw.get("plan_ledger") if isinstance(raw, dict) else None
    pl_dict: dict[str, Any] = pl if isinstance(pl, dict) else {}

    # ledger_dir: config wins, default falls back.
    raw_dir = pl_dict.get("ledger_dir")
    ledger_dir = (
        Path(raw_dir).resolve()
        if isinstance(raw_dir, str) and raw_dir
        else (project_dir / _DEFAULT_LEDGER_DIR).resolve()
    )

    # enabled: precedence is CLI > env > config > auto-detect.
    enabled: bool
    if cli_no_plan_ledger:
        enabled = False
    elif _env_truthy(_ENV_NO_PLAN_LEDGER):
        enabled = False
    elif "enabled" in pl_dict:
        enabled = bool(pl_dict.get("enabled"))
    else:
        enabled = ledger_dir.is_dir()

    # auto_reauthor: same precedence ladder.
    auto_reauthor: bool
    if cli_no_auto_reauthor:
        auto_reauthor = False
    elif _env_truthy(_ENV_NO_AUTO_REAUTHOR):
        auto_reauthor = False
    elif "auto_reauthor" in pl_dict:
        auto_reauthor = bool(pl_dict.get("auto_reauthor"))
    else:
        auto_reauthor = True

    raw_params = pl_dict.get("threshold_params")
    threshold_params = dict(raw_params) if isinstance(raw_params, dict) else {}

    return PlanLedgerSettings(
        enabled=enabled,
        auto_reauthor=auto_reauthor,
        ledger_dir=ledger_dir,
        plan_path=plan_path,
        threshold_params=threshold_params,
    )


__all__ = [
    "PlanLedgerSettings",
    "load_plan_ledger_settings",
]
