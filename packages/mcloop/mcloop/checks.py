"""Run a project's test/lint suite and report results."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Ruff codes we consider safely salvageable by appending `# noqa: CODE`
# to the offending line. These are purely stylistic or cosmetic checks
# that should never block a long-running batch.
# Extend carefully. Logic-bearing checks (F-family unused/undefined,
# bugbear B-family logic traps, security S-family) MUST NOT be added.
_SALVAGEABLE_RUFF_CODES: frozenset[str] = frozenset(
    {
        "E501",  # line too long
        "E741",  # ambiguous variable name
        "W291",  # trailing whitespace
        "W293",  # blank line contains whitespace
    }
)

# Ruff error line: "path/to/file.py:166:100: E501 Line too long ..."
_RUFF_ERROR_RE = re.compile(
    r"^(?P<path>[^:\s][^:]*?):(?P<line>\d+):(?P<col>\d+):\s+(?P<code>[A-Z]+\d+)\b"
)


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
    """Run ruff auto-fixers to clear style issues before verification.

    Runs ``ruff check --fix`` (fixes lint violations) and ``ruff format``
    (reformats to the configured style, which also splits long lines and
    strings where possible). This is a separate step from verification
    so that callers can choose whether to allow side effects. Read-only
    paths (no-op detection, full-suite, stage-boundary) should skip this.

    Both commands are safe to run here: the pipeline snapshots the
    worktree *after* autofix and *before* run_checks, so any changes
    they make are folded into the pending commit. The dirty-worktree
    guard only fires if run_checks itself mutates files, which is
    prevented by using ``ruff format --check .`` (read-only) in the
    check commands.
    """
    project_dir = Path(project_dir)
    for fix_cmd in ["ruff check --fix .", "ruff format ."]:
        try:
            subprocess.run(
                shlex.split(fix_cmd),
                cwd=project_dir,
                capture_output=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            pass


def _parse_ruff_failures(output: str) -> list[tuple[str, int, str]]:
    """Extract (file, line_1indexed, code) tuples from a ruff check output.

    Returns an empty list if no ruff-style error lines are found (so the
    caller can distinguish "ruff errors we can see" from "something else
    broke, like pytest").
    """
    failures: list[tuple[str, int, str]] = []
    for raw in output.splitlines():
        line = raw.strip()
        m = _RUFF_ERROR_RE.match(line)
        if not m:
            continue
        try:
            lineno = int(m.group("line"))
        except ValueError:
            continue
        failures.append((m.group("path"), lineno, m.group("code")))
    return failures


def try_salvage_style_failures(
    project_dir: str | Path,
    failure_output: str,
) -> tuple[bool, list[str]]:
    """Patch minor ruff style violations in place by appending ``# noqa``.

    Only triggers when *every* ruff failure in *failure_output* has a
    code in ``_SALVAGEABLE_RUFF_CODES``. In that case the referenced
    lines are edited to suppress the check for that line only; no
    repo-wide config change is made.

    Returns ``(salvaged, patched_files)``:
    - ``salvaged`` is True if all failures were salvageable and at
      least one line was patched.
    - ``patched_files`` is the list of files whose contents changed,
      for logging.

    A False return means the caller should treat the check failure as
    real. A True return means the caller should re-run the checks and
    commit the patched files along with the batch/task changes.
    """
    project_dir = Path(project_dir)
    failures = _parse_ruff_failures(failure_output)
    if not failures:
        return False, []
    if any(code not in _SALVAGEABLE_RUFF_CODES for _, _, code in failures):
        return False, []

    # Group by file so each file is read/written once.
    by_file: dict[str, list[tuple[int, str]]] = {}
    for path, lineno, code in failures:
        by_file.setdefault(path, []).append((lineno, code))

    patched: list[str] = []
    for rel_path, entries in by_file.items():
        file_path = (project_dir / rel_path).resolve()
        # Sanity: must live inside the project.
        try:
            file_path.relative_to(project_dir.resolve())
        except ValueError:
            continue
        if not file_path.is_file():
            continue
        try:
            text = file_path.read_text()
        except OSError:
            continue
        lines = text.splitlines(keepends=True)
        changed = False
        for lineno, code in entries:
            idx = lineno - 1
            if idx < 0 or idx >= len(lines):
                continue
            original = lines[idx]
            # Preserve the trailing newline (if any) while appending.
            if original.endswith("\n"):
                body, eol = original[:-1], "\n"
            else:
                body, eol = original, ""
            # Skip if this exact noqa already present.
            noqa_re = re.compile(r"#\s*noqa(?::\s*([A-Z0-9, ]+))?\b")
            m = noqa_re.search(body)
            if m:
                existing_codes = m.group(1) or ""
                existing_set = {
                    c.strip() for c in existing_codes.split(",") if c.strip()
                }
                if code in existing_set:
                    continue
                # Extend the existing noqa list.
                if existing_codes:
                    new_codes = ", ".join(sorted(existing_set | {code}))
                else:
                    new_codes = code
                new_body = body[: m.start()] + f"# noqa: {new_codes}" + body[m.end() :]
            else:
                # Append a fresh noqa comment, with a separator if needed.
                sep = "" if body.endswith(" ") or not body.strip() else "  "
                new_body = f"{body}{sep}# noqa: {code}"
            if new_body != body:
                lines[idx] = new_body + eol
                changed = True
        if changed:
            try:
                file_path.write_text("".join(lines))
                patched.append(rel_path)
            except OSError:
                continue

    return bool(patched), patched


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
            commands.append("ruff format --check .")
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
