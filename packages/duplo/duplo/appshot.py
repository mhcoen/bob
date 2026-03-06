"""Capture screenshots of a running app using appshot."""

from __future__ import annotations

import subprocess
from pathlib import Path


def capture_appshot(
    app_name: str,
    output_path: Path | str,
    *,
    launch: str | None = None,
    wait: int = 2,
) -> int:
    """Run ``appshot`` to capture a window screenshot of *app_name*.

    The output directory is created if it does not exist.

    Args:
        app_name: macOS process name of the app to capture (e.g. "MyApp").
        output_path: Destination PNG file path.
        launch: Optional shell command to launch the app before capturing,
            passed as ``--launch <launch>``.
        wait: Seconds to wait after launch before capturing (``--wait``).

    Returns:
        Exit code of the appshot process (0 on success).
    """
    dest = Path(output_path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    cmd = ["appshot", app_name, str(dest), "--wait", str(wait)]
    if launch is not None:
        cmd += ["--launch", launch]

    try:
        result = subprocess.run(cmd)
    except FileNotFoundError:
        print("appshot not found, skipping screenshot.")
        return 1
    return result.returncode
