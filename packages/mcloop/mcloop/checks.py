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


def get_check_commands(project_dir: str | Path) -> list[str]:
    """Return the check commands for this project without running them."""
    project_dir = Path(project_dir)
    config = _load_config(project_dir)
    checks = config.get("checks")
    if isinstance(checks, list) and checks:
        return [str(c) for c in checks]
    return _detect_commands(project_dir, config)


def run_checks(
    project_dir: str | Path,
    changed_files: list[str] | None = None,
) -> CheckResult:
    """Run the project's checks. Returns a CheckResult.

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
        narrowed: list[str] = []
        for cmd in commands:
            if is_test_command(cmd):
                if test_files:
                    narrowed.append(targeted_pytest_command(test_files))
                # else: skip the test command entirely
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
            result = subprocess.run(
                shlex.split(cmd),
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
