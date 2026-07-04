"""Bob umbrella CLI.

Currently provides `bob install`, which installs the combined Telegram + RTK
Claude Code hook (``packages/mcloop/telegram-permission-hook.py``) into
``~/.claude/hooks/`` and registers it as a ``PreToolUse`` hook in
``~/.claude/settings.json``.

The hook does both jobs in one ``PreToolUse`` hook -- Telegram approval in
McLoop sessions and RTK command rewriting in every session -- so there is no
multi-hook ``updatedInput`` race. RTK rewriting is skipped automatically when
``rtk`` is not on ``PATH``, so the hook is safe to install either way.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

HOOK_NAME = "telegram-permission-hook.py"
HOOK_REL = Path("packages") / "mcloop" / HOOK_NAME


def _repo_root() -> Path:
    # bob_tools/bob_cli.py -> bob_tools -> bob-tools -> packages -> <repo root>
    return Path(__file__).resolve().parents[3]


def _default_hook() -> Path:
    return _repo_root() / HOOK_REL


def _entry_refs_hook(entry: dict[str, Any]) -> bool:
    """True if a PreToolUse entry has any hook command referencing the hook file."""
    return any(HOOK_NAME in (h.get("command") or "") for h in entry.get("hooks", []))


def _register_hook(settings_path: Path, command: str) -> tuple[bool, int]:
    """Ensure exactly one PreToolUse hook for the combined hook file.

    Removes any existing PreToolUse entry whose command references
    ``telegram-permission-hook.py`` (any path, e.g. a prior `mcloop install`
    at ~/.mcloop/hooks/), then adds `command`. This keeps a single hook so a
    user who runs both installers never gets duplicate Telegram prompts.

    Returns ``(changed, removed_count)``; ``(False, 0)`` when the only
    hook-referencing entry is already exactly `command`. Backs up
    settings.json before writing.
    """
    if settings_path.exists() and settings_path.read_text().strip():
        data = json.loads(settings_path.read_text())
    else:
        data = {}

    pre = data.setdefault("hooks", {}).setdefault("PreToolUse", [])
    referencing = [e for e in pre if _entry_refs_hook(e)]
    existing_cmds = [h.get("command") for e in referencing for h in e.get("hooks", [])]
    if existing_cmds == [command]:
        return False, 0  # already exactly correct

    removed = len(referencing)
    pre[:] = [e for e in pre if not _entry_refs_hook(e)]
    pre.append({"matcher": "Bash", "hooks": [{"type": "command", "command": command}]})

    if settings_path.exists():
        shutil.copy(settings_path, str(settings_path) + ".bak")
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(data, indent=2) + "\n")
    return True, removed


def cmd_install(args: argparse.Namespace) -> int:
    home = Path(args.home).expanduser() if args.home else Path.home()
    src = Path(args.hook) if args.hook else _default_hook()
    if not src.exists():
        print(
            f"error: combined hook not found at {src}\n"
            "       (expected the Bob repo's packages/mcloop/"
            f"{HOOK_NAME}; pass --hook to override)",
            file=sys.stderr,
        )
        return 1

    hooks_dir = home / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    dst = hooks_dir / HOOK_NAME
    shutil.copy(src, dst)

    command = f"python3 {dst}"
    changed, removed = _register_hook(home / ".claude" / "settings.json", command)

    print(f"installed hook -> {dst}")
    if not changed:
        print("settings.json: already registered")
    elif removed:
        suffix = "y" if removed == 1 else "ies"
        print(
            f"settings.json: registered PreToolUse hook "
            f"(replaced {removed} existing telegram-permission-hook entr{suffix})"
        )
    else:
        print("settings.json: registered PreToolUse hook")
    if shutil.which("rtk") is None:
        print(
            "note: rtk is not on PATH -- the hook will run (Telegram gate only); "
            "install rtk to enable command rewriting."
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bob", description="Bob toolchain CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    install = sub.add_parser(
        "install",
        help=(
            "install the combined Telegram + RTK Claude Code hook into ~/.claude/hooks/"
        ),
    )
    install.add_argument("--home", help="override home directory (for testing)")
    install.add_argument("--hook", help="path to the hook file (default: repo copy)")
    install.set_defaults(func=cmd_install)

    args = parser.parse_args(argv)
    func = cast(Callable[[argparse.Namespace], int], args.func)
    return func(args)


if __name__ == "__main__":
    sys.exit(main())
