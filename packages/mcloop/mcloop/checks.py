"""Run a project's test/lint suite and report results."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CheckResult:
    passed: bool
    output: str
    command: str


def _load_config(project_dir: Path) -> dict:
    """Return parsed mcloop.json if present, else empty dict."""
    config = project_dir / "mcloop.json"
    if not config.exists():
        return {}
    try:
        return json.loads(config.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _normalize_pytest(cmd: str) -> str:
    """Rewrite ``python -m pytest …`` and ``.venv/bin/pytest …`` to ``pytest …``."""
    parts = cmd.split()
    if (
        len(parts) >= 3
        and parts[0] in ("python", "python3")
        and parts[1] == "-m"
        and parts[2] == "pytest"
    ):
        rest = parts[3:]
        return "pytest" + (" " + " ".join(rest) if rest else "")
    if parts and parts[0].endswith("/pytest"):
        rest = parts[1:]
        return "pytest" + (" " + " ".join(rest) if rest else "")
    return cmd


def get_check_commands(project_dir: str | Path) -> list[str]:
    """Return the check commands for this project without running them."""
    project_dir = Path(project_dir)
    config = _load_config(project_dir)
    checks = config.get("checks")
    if isinstance(checks, list) and checks:
        return [_normalize_pytest(str(c)) for c in checks]
    return _detect_commands(project_dir, config)


def run_autofix(project_dir: str | Path) -> None:
    """Run ruff check --fix and ruff format to auto-fix lint issues.

    This is a separate step from verification so that callers can choose
    whether to allow side effects.  Read-only paths (no-op detection,
    full-suite, stage-boundary) should skip this.
    """
    project_dir = Path(project_dir)
    for fix_cmd in ["ruff check --fix .", "ruff format ."]:
        subprocess.run(
            shlex.split(fix_cmd),
            cwd=project_dir,
            capture_output=True,
            timeout=120,
        )


def run_checks(
    project_dir: str | Path,
    changed_files: list[str] | None = None,
) -> CheckResult:
    """Run the project's checks. Returns a CheckResult.

    This function is side-effect-free: it only reads and reports.
    Call *run_autofix()* first if you want auto-formatting applied
    before verification.

    When *changed_files* is provided, test commands (e.g. pytest) are
    scoped to only the test files that correspond to the changed source
    files.  Linters always run in full.  If no matching test files are
    found the test command is skipped entirely.
    """
    from mcloop.targeted import is_test_command, map_to_tests, targeted_pytest_command

    project_dir = Path(project_dir)
    commands = get_check_commands(project_dir)

    if changed_files is not None:
        test_files = map_to_tests(changed_files, project_dir)
        # If Python source files changed but no targeted tests were
        # found (e.g. new module with no test file yet), fall back to
        # the full configured test command rather than skipping tests
        # entirely.  Otherwise untested code could commit.
        py_changed = any(f.endswith(".py") for f in changed_files)
        fallback_to_full = py_changed and not test_files
        narrowed: list[str] = []
        for cmd in commands:
            if is_test_command(cmd):
                if test_files:
                    narrowed.append(targeted_pytest_command(test_files))
                elif fallback_to_full:
                    narrowed.append(cmd)
                # else: no Python changes at all, safe to skip tests
            else:
                narrowed.append(cmd)
        commands = narrowed

    if not commands:
        return CheckResult(
            passed=True,
            output="No check commands detected",
            command="(none)",
        )

    all_output: list[str] = []
    for cmd in commands:
        try:
            parts = shlex.split(cmd)
        except ValueError:
            all_output.append(f"$ {cmd}\nMalformed command (unmatched quotes)")
            return CheckResult(
                passed=False,
                output="\n".join(all_output),
                command=cmd,
            )
        try:
            result = subprocess.run(
                parts,
                shell=False,
                cwd=project_dir,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            all_output.append(f"$ {cmd}\nTIMEOUT after 300s")
            return CheckResult(
                passed=False,
                output="\n".join(all_output),
                command=cmd,
            )
        all_output.append(f"$ {cmd}\n{result.stdout}{result.stderr}")
        if result.returncode != 0:
            return CheckResult(
                passed=False,
                output="\n".join(all_output),
                command=cmd,
            )

    return CheckResult(
        passed=True,
        output="\n".join(all_output),
        command=" && ".join(commands),
    )


def _detect_commands(
    project_dir: Path,
    config: dict,
) -> list[str]:
    """Detect checks from built-in rules and mcloop.json detect rules."""
    commands: list[str] = []

    # Built-in: Python (needs content inspection)
    pyproject = project_dir / "pyproject.toml"
    if pyproject.exists():
        toml_text = pyproject.read_text()
        if "ruff" in toml_text:
            commands.append("ruff check .")
        if "pytest" in toml_text:
            commands.append("pytest")

    # Built-in: Node (needs content inspection)
    pkg_json = project_dir / "package.json"
    if pkg_json.exists():
        pkg = pkg_json.read_text()
        if '"test"' in pkg:
            commands.append("npm test")

    # Swift (--disable-sandbox needed for Claude Code's sandbox)
    if (project_dir / "Package.swift").exists():
        commands.append("swift build --disable-sandbox")

    # Rust
    if (project_dir / "Cargo.toml").exists():
        commands.append("cargo clippy -- -D warnings")
        commands.append("cargo test")

    # Go
    if (project_dir / "go.mod").exists():
        commands.append("go vet ./...")
        commands.append("go test ./...")

    # Java/Kotlin (Gradle)
    if (project_dir / "build.gradle").exists() or (project_dir / "build.gradle.kts").exists():
        commands.append("gradle check")

    # Ruby
    if (project_dir / "Gemfile").exists():
        if (project_dir / ".rubocop.yml").exists():
            commands.append("rubocop")
        commands.append("bundle exec rspec")

    # Make
    if (project_dir / "Makefile").exists():
        commands.append("make check")

    # Marker-based rules from mcloop.json "detect" array
    detect = config.get("detect", [])
    for rule in detect:
        marker = rule.get("marker", "")
        cmds = rule.get("commands", [])
        if not marker or not cmds:
            continue
        if (project_dir / marker).exists():
            commands.extend(cmds)

    return commands


def detect_build(project_dir: str | Path) -> str | None:
    """Auto-detect build command, with mcloop.json override."""
    project_dir = Path(project_dir)
    config = _load_config(project_dir)
    override = config.get("build")
    if override:
        return str(override)

    if (project_dir / "Package.swift").exists():
        return "swift build -c release --disable-sandbox"
    if (project_dir / "Cargo.toml").exists():
        return "cargo build --release"
    if (project_dir / "go.mod").exists():
        return "go build ./..."
    if (project_dir / "package.json").exists():
        pkg = (project_dir / "package.json").read_text()
        if '"build"' in pkg:
            return "npm run build"
    if (project_dir / "build.gradle").exists() or (project_dir / "build.gradle.kts").exists():
        return "gradle build"
    if (project_dir / "Makefile").exists():
        return "make"
    return None


def detect_app_type(project_dir: str | Path) -> str:
    """Classify the app as 'gui', 'cli', or 'web' from the run command.

    Uses the run command (from mcloop.json or auto-detected) and applies
    pattern matching to determine the app type.

    GUI patterns: ``open *.app``, ``./run.sh``
    Web patterns: ``npm start``, ``flask run``, ``uvicorn``, ``gunicorn``,
                  ``python -m http.server``
    CLI: everything else (bare binaries, ``python``, ``cargo run``, etc.)

    Returns 'cli' if no run command is found.
    """
    run_cmd = detect_run(project_dir)
    if not run_cmd:
        return "cli"
    return _classify_run_command(run_cmd)


def _classify_run_command(cmd: str) -> str:
    """Classify a run command string as 'gui', 'cli', or 'web'."""
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return "cli"
    if not parts:
        return "cli"

    base = parts[0]

    # GUI: open *.app
    if base == "open" and any(p.endswith(".app") for p in parts[1:]):
        return "gui"

    # GUI: shell script launcher (./run.sh, ./launch.sh, etc.)
    if re.match(r"^\.?/.*\.sh$", base):
        return "gui"

    # Web: flask run, uvicorn, gunicorn
    web_commands = {"flask", "uvicorn", "gunicorn", "waitress-serve"}
    if base in web_commands:
        return "web"

    # Web: npm start / npm run dev / npm run serve
    if base == "npm" and len(parts) >= 2 and parts[1] in ("start", "run"):
        return "web"

    # Web: python -m http.server / python -m flask
    if base in ("python", "python3") and len(parts) >= 3 and parts[1] == "-m":
        web_modules = {"http.server", "flask", "uvicorn", "gunicorn"}
        if parts[2] in web_modules:
            return "web"

    # CLI: everything else
    return "cli"


def detect_run(project_dir: str | Path) -> str | None:
    """Auto-detect run command, with mcloop.json override."""
    project_dir = Path(project_dir)
    config = _load_config(project_dir)
    override = config.get("run")
    if override:
        return str(override)

    if (project_dir / "Package.swift").exists():
        # Parse target name from Package.swift.
        # If multiple executable targets exist, prefer the one
        # matching the package name (the main app, not a CLI tool).
        try:
            text = (project_dir / "Package.swift").read_text()
            targets = re.findall(
                r'executableTarget\s*\(\s*name:\s*"([^"]+)"',
                text,
            )
            pkg_match = re.search(r'Package\s*\(\s*name:\s*"([^"]+)"', text)
            pkg_name = pkg_match.group(1) if pkg_match else ""
            if targets:
                best = targets[0]
                for t in targets:
                    if t == pkg_name:
                        best = t
                        break
                return f"swift run {best}"
        except OSError:
            pass
        return "swift run"
    if (project_dir / "Cargo.toml").exists():
        return "cargo run"
    if (project_dir / "go.mod").exists():
        return "go run ."
    if (project_dir / "package.json").exists():
        pkg = (project_dir / "package.json").read_text()
        if '"start"' in pkg:
            return "npm start"
    return None
