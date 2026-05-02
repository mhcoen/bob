"""Project-local Orchestra config override detection plus acknowledgment.

The Orchestra configuration system supports two locations: the canonical
``~/.orchestra/config.json`` (global) and an optional
``<project>/.orchestra/config.json`` (project-local override). The
project-local file is intended for advanced users who deliberately want
a workflow to differ for a specific project. Most projects should not
have one.

To make accidental overrides obvious without forcing the user to read
the local file every time, mcloop emits a multi-line banner at the
start of every run when a project-local override is present. The user
can run ``mcloop ack-orchestra-override`` to acknowledge the file. The
acknowledgment is a sha256 fingerprint of the local config bytes
written to ``<project>/.mcloop/orchestra-override-ack``. As long as the
fingerprint matches the current local config, the banner is silenced.
Edits to the local config invalidate the acknowledgment because the
fingerprint changes, and the banner returns until the user re-acks.

This module is the single home for the override-related state:
fingerprint computation, ack file paths and IO, and the banner text.
``mcloop.code_edit`` uses it for the run-time banner.
``mcloop.install_cmd`` uses it for the install-time banner. The new
``mcloop.main._cmd_ack_orchestra_override`` entry point uses it for
``mcloop ack-orchestra-override``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

ACK_FILENAME: str = "orchestra-override-ack"
"""Name of the ack file under ``.mcloop/``. The contents are the sha256
hex digest of the project-local ``.orchestra/config.json`` bytes at
the moment the user acknowledged the override."""

_MCLOOP_DIR: str = ".mcloop"
_ORCHESTRA_DIR: str = ".orchestra"
_ORCHESTRA_CONFIG: str = "config.json"

_BANNER_RULE: str = "=" * 60


def project_orchestra_config_path(project_dir: Path) -> Path:
    """Return the conventional path to the project-local Orchestra config.

    Mirrors ``orchestra.config.project_config_path`` so callers can use
    this module without importing orchestra. orchestra is an optional
    dependency for parts of the project, so this helper is a safe
    fallback that does not require the orchestra package to be present.
    """
    return Path(project_dir) / _ORCHESTRA_DIR / _ORCHESTRA_CONFIG


def ack_path(project_dir: Path) -> Path:
    """Return the path of the ack file for ``project_dir``.

    The ack file lives under ``<project>/.mcloop/orchestra-override-ack``.
    Whether ``.mcloop/`` is git-tracked is the consumer project's
    decision; if it is, the ack survives across machines and clones.
    If ``.mcloop/`` is gitignored, each clone re-acks the first time
    the user runs the subcommand.
    """
    return Path(project_dir) / _MCLOOP_DIR / ACK_FILENAME


def fingerprint(config_path: Path) -> str:
    """Return the sha256 hex digest of ``config_path``'s bytes.

    Raises ``FileNotFoundError`` if the file does not exist. Callers
    should check ``project_orchestra_config_path(project_dir).is_file()``
    before calling.
    """
    with open(config_path, "rb") as fh:
        data = fh.read()
    return hashlib.sha256(data).hexdigest()


def read_ack(project_dir: Path) -> str | None:
    """Return the recorded ack fingerprint or ``None`` if no ack exists.

    The ack file is a single-line text file containing the hex digest.
    Reading is forgiving: any IO error or unexpected content shape
    returns ``None`` so the banner re-fires rather than silently
    treating a corrupted ack as valid.
    """
    p = ack_path(project_dir)
    if not p.is_file():
        return None
    try:
        text = p.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    return text


def write_ack(project_dir: Path, fingerprint_hex: str) -> Path:
    """Write the ack file with ``fingerprint_hex`` and return the path.

    Creates ``<project>/.mcloop/`` if missing. Overwrites an existing
    ack file because re-acknowledging after an edit is the documented
    flow.
    """
    p = ack_path(project_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(fingerprint_hex + "\n", encoding="utf-8")
    return p


def is_acknowledged(project_dir: Path, config_path: Path) -> bool:
    """Return True if the recorded ack matches the current config bytes.

    Returns False when the config does not exist (no override to ack),
    when no ack file exists, or when the recorded fingerprint differs
    from the current one.
    """
    if not config_path.is_file():
        return False
    recorded = read_ack(project_dir)
    if recorded is None:
        return False
    return recorded == fingerprint(config_path)


def banner_lines(config_path: Path) -> list[str]:
    """Return the banner as a list of lines.

    The banner is emitted to stderr by the run-time and install-time
    code paths. Both paths use the same content so the user sees one
    consistent message no matter where they encounter it.

    The banner is bracketed by a rule line so it is hard to miss in a
    long log. Indentation matches the desktop spec exactly.
    """
    abs_path = str(Path(config_path).resolve())
    return [
        _BANNER_RULE,
        "[orchestra] PROJECT-LOCAL OVERRIDE DETECTED",
        "",
        "This project has its own .orchestra/config.json at:",
        f"  {abs_path}",
        "",
        "It overrides ~/.orchestra/config.json for this project. If you",
        "did not create this file deliberately, delete it.",
        "",
        "To silence this banner for this project, run:",
        "  mcloop ack-orchestra-override",
        "",
        _BANNER_RULE,
    ]


def banner_text(config_path: Path) -> str:
    """Return the banner as a single string with trailing newline."""
    return "\n".join(banner_lines(config_path)) + "\n"
