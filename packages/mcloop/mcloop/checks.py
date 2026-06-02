"""Run a project's test/lint suite and report results."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcloop.test_runner import (
    NoTestRunnerAvailableError,
    resolve_test_command,
)

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


def _load_config(project_dir: Path) -> dict[str, Any]:
    """Return parsed mcloop.json if present, else empty dict."""
    config = project_dir / "mcloop.json"
    if not config.exists():
        return {}
    try:
        loaded = json.loads(config.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    return loaded


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


def _resolve_project_venv_command(project_dir: Path, parts: list[str]) -> list[str]:
    """Prefer ``<project>/.venv/bin/<tool>`` for bare check executables."""
    if not parts or "/" in parts[0]:
        return parts
    candidate = project_dir / ".venv" / "bin" / parts[0]
    if candidate.exists():
        return [str(candidate), *parts[1:]]
    return parts


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
        except (subprocess.TimeoutExpired, FileNotFoundError):
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
            # Skip if this exact noqa already present. Require the pragma
            # to be followed by whitespace, end-of-line, or another `#` so
            # comments mentioning "noqa-like" or "noqa_workaround" don't
            # get misclassified as a real pragma and corrupted.
            noqa_re = re.compile(r"#\s*noqa(?::\s*([A-Z0-9, ]+))?(?=\s|#|$)")
            m = noqa_re.search(body)
            if m:
                existing_codes = m.group(1) or ""
                existing_set = {c.strip() for c in existing_codes.split(",") if c.strip()}
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


def _unaccounted_behavioral_changes(
    project_dir: Path,
    accounts: list[Any],
) -> list[str]:
    """Return the sources of unmapped inputs that may change behavior.

    For each unmapped account, decide whether the change can be *proven*
    inert. A Python edit is checked against its HEAD baseline with the
    conservative classifier; anything that is not provably non-behavioral
    (renames, ``__all__`` edits, decorators, new code, a deleted/unreadable
    file, or any non-Python behavior input such as pyproject.toml) is
    flagged. The classifier fails closed: a missing baseline is treated as
    an empty file, so a brand-new module with real code is flagged while a
    new empty/comment-only file is not.
    """
    from mcloop.change_class import ChangeClass, classify_change
    from mcloop.git_ops import read_file_at_head

    flagged: list[str] = []
    for acc in accounts:
        if not getattr(acc, "unmapped", False):
            continue
        src = acc.source
        try:
            new_source: str | None = (project_dir / src).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            new_source = None
        # A removed/unreadable file or any non-Python behavior input cannot
        # be proven inert -- flag it.
        if new_source is None or not src.endswith(".py"):
            flagged.append(src)
            continue
        old_source = read_file_at_head(project_dir, src)
        baseline = old_source if old_source is not None else ""
        if classify_change(baseline, new_source) is ChangeClass.BEHAVIORAL:
            flagged.append(src)
    return flagged


def run_checks(
    project_dir: str | Path,
    changed_files: list[str] | None = None,
) -> CheckResult:
    """Run the project's checks. Returns a CheckResult.

    This function is side-effect-free: it only reads and reports.
    Call *run_autofix()* first if you want auto-formatting applied
    before verification.

    When *changed_files* is provided, both test and lint commands are
    scoped to only the files this batch/task touched. Pytest is scoped
    to tests that map to changed source files. Ruff check/format is
    scoped to the changed Python files directly. Pre-existing errors
    in unrelated files therefore do not block an otherwise clean
    batch. If no matching test files are found the test command is
    skipped; if no Python files changed the linter is skipped.

    When *changed_files* is None (phase boundary, no-op check), every
    command runs against the full repo as configured.
    """
    from mcloop.pytest_signal import pytest_signal_verdict
    from mcloop.targeted import (
        account_changed_inputs,
        is_scoped_python_linter,
        is_test_command,
        targeted_linter_command,
        targeted_pytest_command,
    )

    project_dir = Path(project_dir)
    commands = get_check_commands(project_dir)

    if changed_files is not None:
        accounts = account_changed_inputs(changed_files, project_dir)
        test_files = sorted({f for acc in accounts for f in acc.test_files})
        py_changed = [f for f in changed_files if f.endswith(".py")]

        # Close the mixed-batch fallback hole: an unmapped input that we
        # cannot prove is non-behavioral must FAIL the gate, not merely
        # widen the run. The old full-suite fallback could pass vacuously
        # -- running every test while never exercising the unmapped change
        # -- so an unaccounted behavioral change (a new module with no
        # test, a rename in an untested module, a changed pyproject.toml)
        # would ship green. Provably non-behavioral unmapped edits
        # (comment/docstring/format-only, import reorder) are allowed
        # through and simply contribute no targeted tests. A future waiver
        # path (T-000391) will exempt specific reviewed cases.
        flagged = _unaccounted_behavioral_changes(project_dir, accounts)
        if flagged:
            listed = ", ".join(sorted(flagged))
            return CheckResult(
                passed=False,
                output=(
                    "Gate failed: unaccounted behavioral change(s) with no "
                    f"mapped test: {listed}. Add a test that exercises the "
                    "change so the targeted gate can verify it."
                ),
                command="(gate: unaccounted behavioral change)",
            )

        narrowed: list[str] = []
        for cmd in commands:
            if is_test_command(cmd):
                if test_files:
                    narrowed.append(targeted_pytest_command(test_files))
                # else: no mapped tests and nothing unaccounted, safe to skip
            elif is_scoped_python_linter(cmd):
                # Scope the linter to the Python files this batch/task
                # actually touched. Prevents pre-existing unrelated
                # errors elsewhere in the repo from killing an
                # otherwise clean batch. If no .py files changed,
                # skip the linter entirely (matches the pytest skip
                # behavior for non-Python changes).
                if py_changed:
                    narrowed.append(targeted_linter_command(cmd, py_changed))
                # else: no Python changes, skip the linter
            else:
                narrowed.append(cmd)
        commands = narrowed

    if not commands:
        return CheckResult(
            passed=True,
            output="No check commands detected",
            command="(none)",
        )

    config = _load_config(project_dir)
    try:
        check_timeout = int(config.get("check_timeout", 300))
    except (TypeError, ValueError):
        check_timeout = 300

    def _run_one(cmd: str) -> tuple[bool, str]:
        try:
            parts = shlex.split(cmd)
        except ValueError:
            return False, "Malformed command (unmatched quotes)"
        parts = _resolve_project_venv_command(project_dir, parts)
        try:
            result = subprocess.run(
                parts,
                shell=False,
                cwd=project_dir,
                capture_output=True,
                text=True,
                timeout=check_timeout,
            )
        except subprocess.TimeoutExpired:
            return False, f"TIMEOUT after {check_timeout}s"
        output = f"{result.stdout}{result.stderr}"
        # Pytest can exit 0 while producing no real signal (nothing
        # collected, everything skipped/deselected, or a summary we
        # cannot parse). Treat such a run as a failure so untested code
        # never commits on a vacuous green. Fail closed on the parser
        # sentinel. Non-pytest commands stay judged by exit code alone.
        if is_test_command(cmd):
            valid, reason = pytest_signal_verdict(result.stdout, result.stderr, result.returncode)
            if not valid:
                return False, f"{output}\n[no valid test signal: {reason}]"
        return result.returncode == 0, output

    # Checks must short-circuit: the contract (and
    # test_run_checks_stops_at_first_failure) requires that once a
    # command fails, no subsequent command in the list is executed.
    # An earlier implementation submitted every command to a thread
    # pool eagerly and only *reported* the first failure; that ran
    # every check to completion (side effects and all), violating the
    # contract. Commands are run in list order and execution stops at
    # the first failure. Checks are few and each spawns a subprocess,
    # so the dominant cost is the subprocess itself, not scheduling;
    # sequential ordered execution costs effectively nothing here and
    # makes the short-circuit guarantee unconditional.
    all_output: list[str] = []
    for cmd in commands:
        passed, output = _run_one(cmd)
        all_output.append(f"$ {cmd}\n{output}")
        if not passed:
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
    config: dict[str, Any],
) -> list[str]:
    """Detect checks from built-in rules and mcloop.json detect rules."""
    commands: list[str] = []

    # Built-in: Python (needs content inspection)
    pyproject = project_dir / "pyproject.toml"
    has_mypy_section = False
    if pyproject.exists():
        toml_text = pyproject.read_text()
        if "ruff" in toml_text:
            commands.append("ruff check .")
            commands.append("ruff format --check .")
        if "pytest" in toml_text:
            try:
                commands.append(resolve_test_command(project_dir))
            except NoTestRunnerAvailableError:
                # Preserve legacy behavior when no fallback resolves:
                # appending bare "pytest" lets the subsequent
                # subprocess fail with a clear "command not found"
                # rather than swallowing the project's intent here.
                commands.append("pytest")
        if "[tool.mypy]" in toml_text:
            has_mypy_section = True
    if has_mypy_section or (project_dir / "mypy.ini").exists():
        commands.append("mypy .")

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
