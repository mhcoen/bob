"""Install and uninstall commands for mcloop."""

from __future__ import annotations

import difflib
import json as _json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from mcloop.config import format_reviewer_status


def _print_file_diff(
    path: Path,
    old_content: str,
    new_content: str,
) -> None:
    """Print a unified diff of what a file operation would produce."""
    diff = list(
        difflib.unified_diff(
            old_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=str(path),
            tofile=str(path),
        )
    )
    for line in diff:
        print(f"    {line.rstrip()}")


def _cmd_install(project_dir: Path, *, dry_run: bool = False) -> None:
    """Install mcloop into the project directory."""
    claude_path = shutil.which("claude")
    if not claude_path:
        print(
            "Error: 'claude' not found on PATH.\n"
            "\n"
            "Install Claude Code:\n"
            "  npm install -g @anthropic-ai/claude-code\n"
            "\n"
            "Then re-run: mcloop install",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        result = subprocess.run(
            [claude_path, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        print(
            "Error: 'claude --version' timed out after 10 seconds.\n"
            "Check your Claude Code installation and re-run: mcloop install",
            file=sys.stderr,
        )
        sys.exit(1)
    if result.returncode != 0:
        print(
            f"Error: 'claude --version' failed (exit {result.returncode}).\n"
            "Check your Claude Code installation and re-run: mcloop install",
            file=sys.stderr,
        )
        sys.exit(1)

    version = result.stdout.strip()
    print(f"Found claude: {version}")

    summary: list[tuple[str, str]] = []
    summary.extend(_install_hooks(dry_run=dry_run))
    summary.extend(_merge_settings(dry_run=dry_run))
    summary.append(_setup_telegram(dry_run=dry_run))
    summary.append(_setup_env_security())
    summary.append(_setup_sandbox(dry_run=dry_run))
    summary.append(_install_recommended_permissions(dry_run=dry_run))
    rtk_status = _check_rtk()
    if rtk_status:
        summary.append(rtk_status)

    reviewer_status = _check_reviewer(project_dir)
    if reviewer_status:
        summary.append(reviewer_status)

    _print_install_summary(summary, dry_run=dry_run)


def _print_install_summary(summary: list[tuple[str, str]], *, dry_run: bool = False) -> None:
    """Print a summary table of everything configured, skipped, or pending."""
    prefix = "(dry run) " if dry_run else ""
    print(f"\n{prefix}Install summary:")
    print("  " + "-" * 50)
    for component, status in summary:
        print(f"  {component:<28} {status}")
    print("  " + "-" * 50)

    manual = [(c, s) for c, s in summary if "manual" in s.lower()]
    if manual:
        print("\n  Action needed:")
        for component, status in manual:
            print(f"    - {component}: {status}")
        print()


def _check_rtk() -> tuple[str, str] | None:
    """Print a note if rtk is on PATH."""
    if shutil.which("rtk"):
        print(
            "\n"
            "  Note: RTK detected on PATH.\n"
            "  RTK hooks should be configured separately via: rtk init\n"
        )
        return ("RTK", "detected — configure manually via rtk init")
    return None


def _check_reviewer(project_dir: Path) -> tuple[str, str] | None:
    """Check if .mcloop/config.json has a reviewer section."""
    config_path = project_dir / ".mcloop" / "config.json"
    if not config_path.exists():
        return None
    try:
        data = _json.loads(config_path.read_text())
    except (_json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    reviewer = data.get("reviewer")
    if not isinstance(reviewer, dict):
        return None
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if api_key:
        reviewer = dict(reviewer, api_key=api_key)
    status = format_reviewer_status(reviewer)
    if status:
        return ("Reviewer", status)
    return None


_TELEGRAM_ENV_FILE = Path.home() / ".claude" / "telegram-hook.env"

_TELEGRAM_DESKTOP_MSG = (
    "\n"
    "  Tip: install the Telegram Desktop app alongside the mobile app\n"
    "  so you can approve tool calls from your computer.\n"
)


def _setup_telegram(*, dry_run: bool = False) -> tuple[str, str]:
    """Check for Telegram credentials or prompt interactively."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if token and chat_id:
        print("Telegram: using credentials from environment variables.")
        content = f"TELEGRAM_BOT_TOKEN={token}\nTELEGRAM_CHAT_ID={chat_id}\n"
        if dry_run:
            old = ""
            if _TELEGRAM_ENV_FILE.exists():
                old = _TELEGRAM_ENV_FILE.read_text()
            _print_file_diff(_TELEGRAM_ENV_FILE, old, content)
        else:
            _TELEGRAM_ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
            _TELEGRAM_ENV_FILE.write_text(content)
        print(_TELEGRAM_DESKTOP_MSG)
        return ("Telegram", "configured (env vars)")

    if _TELEGRAM_ENV_FILE.exists():
        print(f"Telegram: using existing credentials from {_TELEGRAM_ENV_FILE}")
        print(_TELEGRAM_DESKTOP_MSG)
        return ("Telegram", "skipped (already configured)")

    print("\nTelegram setup (for remote approval notifications):")
    print("  1. Message @BotFather on Telegram to create a bot")
    print("  2. Copy the bot token")
    print("  3. Send a message to your bot, then get your chat ID\n")

    if dry_run:
        print("  (dry run: skipping interactive prompt)")
        return ("Telegram", "skipped (dry run)")

    try:
        bot_token = input("  Bot token: ").strip()
        if not bot_token:
            print("Skipped: no bot token entered.", file=sys.stderr)
            return ("Telegram", "skipped (no token entered)")
        chat_id_input = input("  Chat ID: ").strip()
        if not chat_id_input:
            print("Skipped: no chat ID entered.", file=sys.stderr)
            return ("Telegram", "skipped (no chat ID entered)")
    except (EOFError, KeyboardInterrupt):
        print("\nSkipped: Telegram setup cancelled.")
        return ("Telegram", "skipped (cancelled)")

    _TELEGRAM_ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    _TELEGRAM_ENV_FILE.write_text(
        f"TELEGRAM_BOT_TOKEN={bot_token}\nTELEGRAM_CHAT_ID={chat_id_input}\n"
    )
    print(f"  Saved credentials to {_TELEGRAM_ENV_FILE}")
    print(_TELEGRAM_DESKTOP_MSG)
    return ("Telegram", "configured")


_MCLOOP_CONFIG = Path.home() / ".mcloop" / "config.json"


def _load_mcloop_config() -> dict:
    """Load ~/.mcloop/config.json, returning {} if missing or invalid."""
    if not _MCLOOP_CONFIG.exists():
        return {}
    try:
        return _json.loads(_MCLOOP_CONFIG.read_text())
    except (_json.JSONDecodeError, OSError):
        return {}


def _setup_env_security() -> tuple[str, str]:
    """Inform the user about the minimal session environment."""
    print(
        "\nSession environment:"
        "\n  mcloop passes only essential variables (PATH, HOME, TERM, etc.)"
        "\n  to CLI subprocesses. API keys, cloud credentials, and tokens"
        "\n  are excluded by default, so the CLI uses your subscription."
        '\n  To use API billing instead, set "billing": "api" in config.'
        "\n  mcloop will pass the appropriate key for the active CLI."
        f"\n  Config: {_MCLOOP_CONFIG}\n"
    )
    config = _load_mcloop_config()
    billing = config.get("billing", "subscription")
    return ("Environment", f"minimal ({billing} billing)")


_CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"

_SANDBOX_DEFAULTS = {
    "enabled": True,
    "autoAllowBashIfSandboxed": True,
    "allowUnsandboxedCommands": False,
}


def _setup_sandbox(*, dry_run: bool = False) -> tuple[str, str]:
    """Ask whether to enable Claude Code sandbox. Will enable, never disable."""
    settings_path = _CLAUDE_SETTINGS

    original_content = ""
    settings: dict = {}
    if settings_path.exists():
        original_content = settings_path.read_text()
        try:
            settings = _json.loads(original_content)
        except _json.JSONDecodeError:
            print(
                f"Error: {settings_path} contains invalid JSON.",
                file=sys.stderr,
            )
            sys.exit(1)

    if not isinstance(settings, dict):
        print(
            f"Error: {settings_path} is not a JSON object.",
            file=sys.stderr,
        )
        sys.exit(1)

    sandbox = settings.get("sandbox", {})
    if isinstance(sandbox, dict) and sandbox.get("enabled") is True:
        print("Sandbox: already enabled (skipping).")
        return ("Sandbox", "skipped (already enabled)")

    print(
        "\nSandbox mode:"
        "\n  The sandbox restricts file system and network access for"
        "\n  Claude Code sessions, adding a layer of protection when"
        "\n  running unattended.\n"
    )

    if dry_run:
        sandbox_cfg = dict(_SANDBOX_DEFAULTS)
        if isinstance(sandbox, dict):
            new_sandbox = dict(sandbox)
            new_sandbox.update(sandbox_cfg)
        else:
            new_sandbox = sandbox_cfg
        settings["sandbox"] = new_sandbox
        new_content = _json.dumps(settings, indent=2) + "\n"
        _print_file_diff(settings_path, original_content, new_content)
        print("  (dry run: would enable sandbox by default)")
        return ("Sandbox", "would enable (dry run)")

    try:
        answer = input("  Enable sandbox? [Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nSkipped: sandbox not enabled.")
        return ("Sandbox", "skipped (cancelled)")

    if answer in ("n", "no"):
        print("  Sandbox: not enabled.")
        return ("Sandbox", "not enabled")

    sandbox_cfg = dict(_SANDBOX_DEFAULTS)
    if isinstance(sandbox, dict):
        sandbox.update(sandbox_cfg)
    else:
        sandbox = sandbox_cfg
    settings["sandbox"] = sandbox

    if not dry_run:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(_json.dumps(settings, indent=2) + "\n")
    print("  Sandbox: enabled.")
    print(f"  Saved to {settings_path}")
    return ("Sandbox", "configured (enabled)")


_RECOMMENDED_PERMS_DEST = Path.home() / ".mcloop" / "recommended-permissions.json"


def _install_recommended_permissions(
    *,
    dry_run: bool = False,
) -> tuple[str, str]:
    """Install recommended permissions baseline for manual merging."""
    repo_root = Path(__file__).resolve().parent.parent
    src = repo_root / "settings.example.json"

    if not src.exists():
        print(
            f"Warning: settings.example.json not found: {src}",
            file=sys.stderr,
        )
        return ("Permissions", "warning (settings.example.json not found)")

    raw = src.read_text()
    try:
        example = _json.loads(raw)
    except _json.JSONDecodeError:
        print(
            f"Warning: settings.example.json contains invalid JSON: {src}",
            file=sys.stderr,
        )
        return ("Permissions", "warning (invalid JSON)")

    perms = example.get("permissions", {})
    if not isinstance(perms, dict):
        perms = {}

    allow = perms.get("allow", [])
    recommended = {"permissions": {"allow": allow}}

    dest = _RECOMMENDED_PERMS_DEST
    new_content = _json.dumps(recommended, indent=2) + "\n"
    if dry_run:
        old = dest.read_text() if dest.exists() else ""
        _print_file_diff(dest, old, new_content)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(new_content)
        print(f"  installed: {dest}")

    print(
        "\n  McLoop does not modify runtime permissions."
        "\n  Recommended permission settings are provided in:"
        f"\n    {dest}"
        "\n  Merge them into ~/.claude/settings.json manually"
        "\n  if desired.\n"
    )
    return ("Permissions", "installed — merge manually")


# Hook scripts to copy: (source filename in repo root, dest filename)
_HOOK_SCRIPTS = [
    "telegram-permission-hook.py",
    "session-start-hook.py",
]


def _install_hooks(
    *,
    dry_run: bool = False,
) -> list[tuple[str, str]]:
    """Copy hook scripts to ~/.mcloop/hooks/. Skip if already present."""
    repo_root = Path(__file__).resolve().parent.parent
    hooks_dir = Path.home() / ".mcloop" / "hooks"

    if not dry_run:
        hooks_dir.mkdir(parents=True, exist_ok=True)

    results: list[tuple[str, str]] = []
    for script_name in _HOOK_SCRIPTS:
        src = repo_root / script_name
        dest = hooks_dir / script_name
        label = f"Hook ({script_name})"

        if not src.exists():
            print(
                f"Warning: hook source not found: {src}",
                file=sys.stderr,
            )
            results.append((label, "warning (source not found)"))
            continue

        if dest.exists():
            print(f"  skip (exists): {dest}")
            results.append((label, "skipped (already installed)"))
            continue

        if dry_run:
            print(f"  would copy: {src} -> {dest}")
            results.append((label, "would install (dry run)"))
        else:
            shutil.copy2(src, dest)
            print(f"  copied: {dest}")
            results.append((label, "installed"))
    return results


# Hook entries to merge into ~/.claude/settings.json
_HOOK_ENTRIES = {
    "hooks": {
        "PreToolUse": [
            {
                "type": "command",
                "command": "python3 ~/.mcloop/hooks/telegram-permission-hook.py",
            },
        ],
        "SessionStart": [
            {
                "type": "command",
                "command": "python3 ~/.mcloop/hooks/session-start-hook.py",
            },
        ],
    },
}


def _merge_settings(
    *,
    dry_run: bool = False,
) -> list[tuple[str, str]]:
    """Merge mcloop hook entries into ~/.claude/settings.json."""
    settings_path = Path.home() / ".claude" / "settings.json"
    original_content = ""

    if settings_path.exists():
        original_content = settings_path.read_text()
        try:
            settings = _json.loads(original_content)
        except _json.JSONDecodeError:
            print(
                f"Error: {settings_path} contains invalid JSON.",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        settings = {}

    if not isinstance(settings, dict):
        print(
            f"Error: {settings_path} is not a JSON object.",
            file=sys.stderr,
        )
        sys.exit(1)

    hooks = settings.setdefault("hooks", {})
    changed = False
    results: list[tuple[str, str]] = []

    for event_name, entries in _HOOK_ENTRIES["hooks"].items():
        existing = hooks.setdefault(event_name, [])
        existing_commands = {e.get("command") for e in existing if isinstance(e, dict)}
        for entry in entries:
            label = f"Settings ({event_name})"
            if entry["command"] in existing_commands:
                print(f"  skip (exists): hooks.{event_name}: {entry['command']}")
                results.append((label, "skipped (already configured)"))
            else:
                existing.append(entry)
                changed = True
                if dry_run:
                    print(f"  would add: hooks.{event_name}: {entry['command']}")
                    results.append((label, "would add (dry run)"))
                else:
                    print(f"  added: hooks.{event_name}: {entry['command']}")
                    results.append((label, "configured"))

    if changed:
        new_content = _json.dumps(settings, indent=2) + "\n"
        if dry_run:
            _print_file_diff(settings_path, original_content, new_content)
        else:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(new_content)
    return results


def _unmerge_settings(
    *,
    dry_run: bool = False,
) -> list[tuple[str, str]]:
    """Remove mcloop hook entries from ~/.claude/settings.json."""
    settings_path = Path.home() / ".claude" / "settings.json"
    results: list[tuple[str, str]] = []

    if not settings_path.exists():
        print("  skip: ~/.claude/settings.json does not exist")
        results.append(("Settings", "skipped (no settings file)"))
        return results

    original_content = settings_path.read_text()
    try:
        settings = _json.loads(original_content)
    except _json.JSONDecodeError:
        print(
            f"Error: {settings_path} contains invalid JSON.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not isinstance(settings, dict):
        print(
            f"Error: {settings_path} is not a JSON object.",
            file=sys.stderr,
        )
        sys.exit(1)

    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict):
        print("  skip: hooks is not an object")
        results.append(("Settings", "skipped (hooks not an object)"))
        return results

    changed = False
    for event_name in list(hooks.keys()):
        entries = hooks[event_name]
        if not isinstance(entries, list):
            continue
        before = len(entries)
        kept = [
            e
            for e in entries
            if not (isinstance(e, dict) and "~/.mcloop/hooks/" in e.get("command", ""))
        ]
        removed_count = before - len(kept)
        if removed_count > 0:
            label = f"Settings ({event_name})"
            for e in entries:
                if isinstance(e, dict) and "~/.mcloop/hooks/" in e.get("command", ""):
                    if dry_run:
                        print(f"  would remove: hooks.{event_name}: {e['command']}")
                        results.append((label, "would remove (dry run)"))
                    else:
                        print(f"  removed: hooks.{event_name}: {e['command']}")
                        results.append((label, "removed"))
            hooks[event_name] = kept
            changed = True
            if not kept:
                del hooks[event_name]
        else:
            label = f"Settings ({event_name})"
            results.append((label, "skipped (no mcloop entries)"))

    if not hooks and "hooks" in settings:
        del settings["hooks"]

    if changed:
        new_content = _json.dumps(settings, indent=2) + "\n"
        if dry_run:
            _print_file_diff(settings_path, original_content, new_content)
        else:
            settings_path.write_text(new_content)

    if not results:
        results.append(("Settings", "skipped (no hook entries)"))

    return results


def _remove_telegram_env(*, dry_run: bool = False) -> tuple[str, str]:
    """Remove ~/.claude/telegram-hook.env if it exists."""
    component = "telegram-hook.env"
    if not _TELEGRAM_ENV_FILE.exists():
        print(f"  {_TELEGRAM_ENV_FILE}: not found, nothing to remove")
        return (component, "skipped (not found)")
    if dry_run:
        print(f"  Would remove {_TELEGRAM_ENV_FILE}")
        return (component, "would remove")
    _TELEGRAM_ENV_FILE.unlink()
    print(f"  Removed {_TELEGRAM_ENV_FILE}")
    return (component, "removed")


def _remove_hooks_dir(
    *,
    dry_run: bool = False,
) -> list[tuple[str, str]]:
    """Remove ~/.mcloop/hooks/ directory."""
    hooks_dir = Path.home() / ".mcloop" / "hooks"
    if not hooks_dir.exists():
        print(f"  {hooks_dir}: not found, nothing to remove")
        return [("hooks directory", "skipped (not found)")]
    if dry_run:
        files = sorted(hooks_dir.rglob("*"))
        if files:
            for f in files:
                if f.is_file():
                    print(f"  Would delete {f}")
        else:
            print(f"  Would remove {hooks_dir} (empty)")
        return [
            (f"hooks/{f.relative_to(hooks_dir)}", "would remove") for f in files if f.is_file()
        ] or [("hooks directory", "would remove")]
    shutil.rmtree(hooks_dir)
    print(f"  Removed {hooks_dir}")
    return [("hooks directory", "removed")]


def _remove_config_json(*, dry_run: bool = False) -> tuple[str, str]:
    """Remove ~/.mcloop/config.json."""
    component = "config.json"
    if not _MCLOOP_CONFIG.exists():
        print(f"  {_MCLOOP_CONFIG}: not found, nothing to remove")
        return (component, "skipped (not found)")
    if dry_run:
        print(f"  Would remove {_MCLOOP_CONFIG}")
        return (component, "would remove")
    _MCLOOP_CONFIG.unlink()
    print(f"  Removed {_MCLOOP_CONFIG}")
    return (component, "removed")


def _remove_recommended_perms(*, dry_run: bool = False) -> tuple[str, str]:
    """Remove ~/.mcloop/recommended-permissions.json."""
    component = "recommended-permissions.json"
    if not _RECOMMENDED_PERMS_DEST.exists():
        print(f"  {_RECOMMENDED_PERMS_DEST}: not found, nothing to remove")
        return (component, "skipped (not found)")
    if dry_run:
        print(f"  Would remove {_RECOMMENDED_PERMS_DEST}")
        return (component, "would remove")
    _RECOMMENDED_PERMS_DEST.unlink()
    print(f"  Removed {_RECOMMENDED_PERMS_DEST}")
    return (component, "removed")


def _print_uninstall_summary(summary: list[tuple[str, str]], *, dry_run: bool = False) -> None:
    """Print a summary table of what was removed and what was left."""
    prefix = "(dry run) " if dry_run else ""
    print(f"\n{prefix}Uninstall summary:")
    print("  " + "-" * 50)

    removed = [(c, s) for c, s in summary if "removed" in s and "would" not in s]
    would_remove = [(c, s) for c, s in summary if "would remove" in s]
    skipped = [(c, s) for c, s in summary if "skipped" in s]
    left = [(c, s) for c, s in summary if s == "left in place"]

    if removed:
        print("  Removed:")
        for component, status in removed:
            print(f"    {component:<28} {status}")
    if would_remove:
        print("  Would remove:")
        for component, status in would_remove:
            print(f"    {component:<28} {status}")
    if skipped:
        print("  Already absent:")
        for component, status in skipped:
            print(f"    {component:<28} {status}")
    if left:
        print("  Left in place:")
        for component, _status in left:
            print(f"    {component}")
    print("  " + "-" * 50)


_UNINSTALL_LEFT_IN_PLACE = [
    ("permissions.allow entries", "left in place"),
    ("project-level .mcloop/ directories", "left in place"),
    ("PLAN.md files", "left in place"),
    ("logs/ directories", "left in place"),
    ("sandbox settings", "left in place"),
]


def _cmd_uninstall(project_dir: Path, *, dry_run: bool = False) -> None:
    """Remove mcloop hook entries and credential files."""
    prefix = "[dry run] " if dry_run else ""
    print(f"\n{prefix}mcloop uninstall\n")
    summary: list[tuple[str, str]] = []
    print("Removing hook entries from ~/.claude/settings.json...")
    summary.extend(_unmerge_settings(dry_run=dry_run))
    print("\nRemoving Telegram credentials...")
    summary.append(_remove_telegram_env(dry_run=dry_run))
    print("\nRemoving hooks directory...")
    summary.extend(_remove_hooks_dir(dry_run=dry_run))
    print("\nRemoving config.json...")
    summary.append(_remove_config_json(dry_run=dry_run))
    print("\nRemoving recommended-permissions.json...")
    summary.append(_remove_recommended_perms(dry_run=dry_run))
    summary.extend(_UNINSTALL_LEFT_IN_PLACE)
    _print_uninstall_summary(summary, dry_run=dry_run)
    print("\nDone.")
