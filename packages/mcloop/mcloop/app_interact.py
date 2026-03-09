"""macOS GUI app interaction via osascript/System Events."""

from __future__ import annotations

import subprocess


def _run_osascript(script: str, timeout: float = 10.0) -> str:
    """Run an AppleScript via osascript and return stdout.

    Raises RuntimeError on non-zero exit or timeout.
    """
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise RuntimeError("osascript not found (not macOS?)")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"osascript timed out after {timeout}s")
    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(f"osascript failed ({result.returncode}): {stderr}")
    return result.stdout.strip()


def click_button(app_name: str, button_label: str) -> None:
    """Click a button by its accessibility label in an app's front window."""
    script = (
        f'tell application "System Events"\n'
        f'  tell process "{app_name}"\n'
        f'    click button "{button_label}" of window 1\n'
        f"  end tell\n"
        f"end tell"
    )
    _run_osascript(script)


def select_menu_item(app_name: str, *menu_path: str) -> None:
    """Select a menu item by path (e.g. "File", "Save As...").

    Example: select_menu_item("TextEdit", "File", "Save As...")
    """
    if len(menu_path) < 2:
        raise ValueError("menu_path must have at least 2 elements (menu, item)")
    parts = ["menu bar 1"]
    for i, name in enumerate(menu_path):
        if i == 0:
            parts.append(f'menu bar item "{name}"')
            parts.append(f'menu "{name}"')
        elif i < len(menu_path) - 1:
            parts.append(f'menu item "{name}"')
            parts.append(f'menu "{name}"')
        else:
            parts.append(f'menu item "{name}"')
    chain = " of ".join(reversed(parts))
    script = (
        f'tell application "System Events"\n'
        f'  tell process "{app_name}"\n'
        f"    click {chain}\n"
        f"  end tell\n"
        f"end tell"
    )
    _run_osascript(script)


def type_text(text: str) -> None:
    """Type text into the currently focused field via System Events."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    script = f'tell application "System Events"\n  keystroke "{escaped}"\nend tell'
    _run_osascript(script)


def read_value(app_name: str, element_type: str, label: str) -> str:
    """Read the value of a UI element by type and label.

    Example: read_value("MyApp", "text field", "Username")
    """
    script = (
        f'tell application "System Events"\n'
        f'  tell process "{app_name}"\n'
        f'    get value of {element_type} "{label}" of window 1\n'
        f"  end tell\n"
        f"end tell"
    )
    return _run_osascript(script)


def list_elements(app_name: str) -> str:
    """List all UI elements in an app's front window.

    Returns the raw osascript output describing the element tree.
    """
    script = (
        f'tell application "System Events"\n'
        f'  tell process "{app_name}"\n'
        f"    entire contents of window 1\n"
        f"  end tell\n"
        f"end tell"
    )
    return _run_osascript(script)


def window_exists(app_name: str) -> bool:
    """Check if an app has at least one window open."""
    script = (
        f'tell application "System Events"\n'
        f'  tell process "{app_name}"\n'
        f"    count of windows\n"
        f"  end tell\n"
        f"end tell"
    )
    try:
        result = _run_osascript(script)
        return int(result) > 0
    except (RuntimeError, ValueError):
        return False


def screenshot_window(app_name: str, output_path: str) -> None:
    """Take a screenshot of an app's front window.

    Uses screencapture -l with the window ID obtained from
    System Events.
    """
    wid_script = (
        f'tell application "System Events"\n'
        f'  tell process "{app_name}"\n'
        f"    set wid to id of window 1\n"
        f"  end tell\n"
        f"end tell\n"
        f"return wid"
    )
    window_id = _run_osascript(wid_script)
    try:
        subprocess.run(
            ["screencapture", "-l", window_id, output_path],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        raise RuntimeError("screencapture not found (not macOS?)")
    except subprocess.TimeoutExpired:
        raise RuntimeError("screencapture timed out")
