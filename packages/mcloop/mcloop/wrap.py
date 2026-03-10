"""Instrument project source files with error-catching hooks.

Detects project language from PLAN.md description, file extensions,
or build system files. Supports Swift and Python initially. Injected
code is delimited with markers so it can be detected and re-injected.
"""

from __future__ import annotations

import re
from pathlib import Path

SWIFT_BEGIN = "// mcloop:wrap:begin"
SWIFT_END = "// mcloop:wrap:end"
PYTHON_BEGIN = "# mcloop:wrap:begin"
PYTHON_END = "# mcloop:wrap:end"

SWIFT_WRAPPER = """\
// mcloop:wrap:begin
import Foundation

/// Registry for app-state providers. Each closure returns a dict of
/// property names to their string representations, captured at crash
/// time.  ObservableObject subclasses register themselves so that
/// @Published properties are included in error reports.
enum _McloopState {
    private static var _providers: [() -> [String: String]] = []
    private static var _lastAction: String = ""
    static let lock = NSLock()

    static func register(_ provider: @escaping () -> [String: String]) {
        lock.lock()
        _providers.append(provider)
        lock.unlock()
    }

    static func recordAction(_ action: String) {
        lock.lock()
        _lastAction = action
        lock.unlock()
    }

    static func snapshot() -> [String: String] {
        lock.lock()
        let providers = _providers
        lock.unlock()
        var result: [String: String] = [:]
        for provider in providers {
            for (k, v) in provider() {
                result[k] = v
            }
        }
        return result
    }

    static func lastAction() -> String {
        lock.lock()
        defer { lock.unlock() }
        return _lastAction
    }
}

private func _mcloopSetupCrashHandlers() {
    let errorDir = FileManager.default.currentDirectoryPath + "/.mcloop"
    try? FileManager.default.createDirectory(
        atPath: errorDir, withIntermediateDirectories: true)

    NSSetUncaughtExceptionHandler { exception in
        let state = _McloopState.snapshot()
        let action = _McloopState.lastAction()
        let report: [String: Any] = [
            "timestamp": ISO8601DateFormatter().string(from: Date()),
            "exception_type": String(describing: type(of: exception)),
            "description": exception.reason ?? exception.name.rawValue,
            "stack_trace": exception.callStackSymbols.joined(
                separator: "\\n"),
            "source_file": "",
            "line": 0,
            "app_state": state,
            "last_action": action,
            "fix_attempts": 0,
        ]
        _mcloopWriteError(report, dir: errorDir)
    }

    for sig: Int32 in [SIGSEGV, SIGABRT, SIGBUS] {
        signal(sig) { signum in
            let state = _McloopState.snapshot()
            let action = _McloopState.lastAction()
            let report: [String: Any] = [
                "timestamp": ISO8601DateFormatter().string(from: Date()),
                "signal": signum,
                "exception_type": "Signal",
                "description": "Received signal \\(signum)",
                "stack_trace": Thread.callStackSymbols.joined(
                    separator: "\\n"),
                "source_file": "",
                "line": 0,
                "app_state": state,
                "last_action": action,
                "fix_attempts": 0,
            ]
            _mcloopWriteError(report, dir: errorDir)
            Darwin.signal(signum, SIG_DFL)
            Darwin.raise(signum)
        }
    }
}

private func _mcloopWriteError(_ report: [String: Any], dir: String) {
    let path = dir + "/errors.json"
    var entries: [[String: Any]] = []
    if let data = FileManager.default.contents(atPath: path),
       let existing = try? JSONSerialization.jsonObject(
        with: data) as? [[String: Any]]
    {
        entries = existing
    }
    var entry = report
    let trace = (report["stack_trace"] as? String) ?? ""
    let sig = report.hashValue
    entry["id"] = String(format: "%08x", abs(trace.hashValue ^ sig))
    entries.append(entry)
    if let data = try? JSONSerialization.data(
        withJSONObject: entries, options: [.prettyPrinted])
    {
        FileManager.default.createFile(atPath: path, contents: data)
    }
}
// mcloop:wrap:end
"""

PYTHON_WRAPPER = """\
# mcloop:wrap:begin
import hashlib as _mcloop_hashlib
import json as _mcloop_json
import logging as _mcloop_logging
import signal as _mcloop_signal
import sys as _mcloop_sys
import traceback as _mcloop_traceback
from datetime import datetime as _mcloop_datetime, timezone as _mcloop_tz
from pathlib import Path as _mcloop_Path


class _McloopState:
    _providers = []
    _last_action = ""

    @classmethod
    def register(cls, provider):
        cls._providers.append(provider)

    @classmethod
    def record_action(cls, action):
        cls._last_action = str(action)

    @classmethod
    def snapshot(cls):
        result = {}
        for provider in cls._providers:
            try:
                result.update(provider())
            except Exception:
                pass
        return result

    @classmethod
    def last_action(cls):
        return cls._last_action


def _mcloop_setup_crash_handlers():
    error_dir = _mcloop_Path(".mcloop")
    error_dir.mkdir(parents=True, exist_ok=True)
    error_path = error_dir / "errors.json"

    def _write_error(report):
        entries = []
        if error_path.exists():
            try:
                entries = _mcloop_json.loads(error_path.read_text())
            except (ValueError, OSError):
                pass
        trace = report.get("stack_trace", "")
        sig = report.get("signal", report.get("exception_type", ""))
        raw = f"{trace}{sig}".encode()
        report["id"] = _mcloop_hashlib.md5(raw).hexdigest()[:8]
        entries.append(report)
        try:
            error_path.write_text(
                _mcloop_json.dumps(entries, indent=2) + "\\n"
            )
        except OSError:
            pass

    def _excepthook(exc_type, exc_value, exc_tb):
        frames = _mcloop_traceback.extract_tb(exc_tb)
        last = frames[-1] if frames else None
        local_vars = {}
        if exc_tb is not None:
            tb = exc_tb
            while tb.tb_next:
                tb = tb.tb_next
            local_vars = {
                k: repr(v)
                for k, v in tb.tb_frame.f_locals.items()
                if not k.startswith("_")
            }
        state = _McloopState.snapshot()
        state.update(local_vars)
        report = {
            "timestamp": _mcloop_datetime.now(
                _mcloop_tz.utc
            ).isoformat(),
            "exception_type": exc_type.__name__,
            "description": str(exc_value),
            "stack_trace": "".join(
                _mcloop_traceback.format_exception(
                    exc_type, exc_value, exc_tb
                )
            ),
            "source_file": last.filename if last else "",
            "line": last.lineno if last else 0,
            "app_state": state,
            "last_action": _McloopState.last_action(),
            "fix_attempts": 0,
        }
        _write_error(report)
        _mcloop_sys.__excepthook__(exc_type, exc_value, exc_tb)

    _mcloop_sys.excepthook = _excepthook

    def _signal_handler(signum, frame):
        source = ""
        lineno = 0
        if frame is not None:
            source = frame.f_code.co_filename
            lineno = frame.f_lineno
        report = {
            "timestamp": _mcloop_datetime.now(
                _mcloop_tz.utc
            ).isoformat(),
            "signal": signum,
            "exception_type": "Signal",
            "description": f"Received signal {signum}",
            "stack_trace": "".join(_mcloop_traceback.format_stack(frame)),
            "source_file": source,
            "line": lineno,
            "app_state": _McloopState.snapshot(),
            "last_action": _McloopState.last_action(),
            "fix_attempts": 0,
        }
        _write_error(report)
        _mcloop_signal.signal(signum, _mcloop_signal.SIG_DFL)
        import os
        os.kill(os.getpid(), signum)

    for _sig in (
        _mcloop_signal.SIGSEGV,
        _mcloop_signal.SIGABRT,
    ):
        try:
            _mcloop_signal.signal(_sig, _signal_handler)
        except OSError:
            pass

    class _McloopLogHandler(_mcloop_logging.Handler):
        def emit(self, record):
            if record.exc_info and record.exc_info[1] is not None:
                exc_type, exc_value, exc_tb = record.exc_info
                frames = _mcloop_traceback.extract_tb(exc_tb)
                last = frames[-1] if frames else None
                local_vars = {}
                if exc_tb is not None:
                    tb = exc_tb
                    while tb.tb_next:
                        tb = tb.tb_next
                    local_vars = {
                        k: repr(v)
                        for k, v in tb.tb_frame.f_locals.items()
                        if not k.startswith("_")
                    }
                state = _McloopState.snapshot()
                state.update(local_vars)
                report = {
                    "timestamp": _mcloop_datetime.now(
                        _mcloop_tz.utc
                    ).isoformat(),
                    "exception_type": exc_type.__name__,
                    "description": str(exc_value),
                    "stack_trace": "".join(
                        _mcloop_traceback.format_exception(
                            exc_type, exc_value, exc_tb
                        )
                    ),
                    "source_file": last.filename if last else "",
                    "line": last.lineno if last else 0,
                    "app_state": state,
                    "last_action": _McloopState.last_action(),
                    "fix_attempts": 0,
                }
                _write_error(report)

    handler = _McloopLogHandler()
    handler.setLevel(_mcloop_logging.ERROR)
    _mcloop_logging.getLogger().addHandler(handler)


_mcloop_setup_crash_handlers()
# mcloop:wrap:end
"""


def detect_language(project_dir: Path) -> str | None:
    """Detect project language from PLAN.md, file extensions, or build system.

    Returns 'swift', 'python', or None if unsupported/undetectable.
    """
    # 1. Check PLAN.md description for language keywords
    plan = project_dir / "PLAN.md"
    if plan.exists():
        try:
            text = plan.read_text().lower()
            # Check description (before first checkbox)
            desc = text.split("- [")[0] if "- [" in text else text
            if _match_language(desc):
                return _match_language(desc)
        except OSError:
            pass

    # 2. Check file extensions
    lang = _detect_from_extensions(project_dir)
    if lang:
        return lang

    # 3. Check build system files
    if (project_dir / "Package.swift").exists():
        return "swift"
    if (project_dir / "pyproject.toml").exists():
        return "python"
    if (project_dir / "setup.py").exists():
        return "python"

    return None


def _match_language(text: str) -> str | None:
    """Match language keywords in text."""
    swift_patterns = [
        r"\bswift\b",
        r"\bswiftui\b",
        r"\bspm\b",
        r"\bxcode\b",
        r"\buikit\b",
        r"\bappkit\b",
    ]
    python_patterns = [
        r"\bpython\b",
        r"\bdjango\b",
        r"\bflask\b",
        r"\bfastapi\b",
        r"\bpytest\b",
    ]
    for pat in swift_patterns:
        if re.search(pat, text):
            return "swift"
    for pat in python_patterns:
        if re.search(pat, text):
            return "python"
    return None


def _detect_from_extensions(project_dir: Path) -> str | None:
    """Detect language from file extensions in the project."""
    swift_count = 0
    python_count = 0
    for p in project_dir.rglob("*"):
        if p.is_dir():
            continue
        # Skip hidden dirs and common non-source dirs
        parts = p.parts
        if any(
            part.startswith(".") or part in ("node_modules", "__pycache__", ".build")
            for part in parts
        ):
            continue
        if p.suffix == ".swift":
            swift_count += 1
        elif p.suffix == ".py":
            python_count += 1
    if swift_count > python_count and swift_count > 0:
        return "swift"
    if python_count > swift_count and python_count > 0:
        return "python"
    return None


def find_entry_point(project_dir: Path, language: str) -> Path | None:
    """Find the main entry point file for the given language.

    Swift: looks for @main struct or App.swift or main.swift
    Python: looks for __main__.py or main.py
    """
    if language == "swift":
        return _find_swift_entry(project_dir)
    if language == "python":
        return _find_python_entry(project_dir)
    return None


def _find_swift_entry(project_dir: Path) -> Path | None:
    """Find Swift app entry point."""
    swift_files = []
    for p in project_dir.rglob("*.swift"):
        parts = p.parts
        if any(part.startswith(".") or part == ".build" for part in parts):
            continue
        swift_files.append(p)

    # First: look for @main attribute
    for f in swift_files:
        try:
            text = f.read_text()
            if re.search(r"@main\b", text):
                return f
        except OSError:
            continue

    # Second: files named *App.swift (SwiftUI convention)
    for f in swift_files:
        if f.stem.endswith("App"):
            return f

    # Third: main.swift
    for f in swift_files:
        if f.name == "main.swift":
            return f

    return None


def _find_python_entry(project_dir: Path) -> Path | None:
    """Find Python app entry point."""
    # Look for __main__.py in any package
    for p in project_dir.rglob("__main__.py"):
        parts = p.parts
        if any(part.startswith(".") or part in ("node_modules", "__pycache__") for part in parts):
            continue
        return p

    # Look for main.py at project root
    main = project_dir / "main.py"
    if main.exists():
        return main

    # Look for main.py anywhere
    for p in project_dir.rglob("main.py"):
        parts = p.parts
        if any(part.startswith(".") or part in ("node_modules", "__pycache__") for part in parts):
            continue
        return p

    return None


def has_markers(content: str, language: str) -> bool:
    """Check if file content already has mcloop wrap markers."""
    if language == "swift":
        return SWIFT_BEGIN in content and SWIFT_END in content
    if language == "python":
        return PYTHON_BEGIN in content and PYTHON_END in content
    return False


def strip_markers(content: str, language: str) -> str:
    """Remove existing mcloop wrap block from file content."""
    if language == "swift":
        begin, end = SWIFT_BEGIN, SWIFT_END
    elif language == "python":
        begin, end = PYTHON_BEGIN, PYTHON_END
    else:
        return content

    lines = content.splitlines(keepends=True)
    result = []
    inside = False
    for line in lines:
        if begin in line:
            inside = True
            continue
        if end in line:
            inside = False
            continue
        if not inside:
            result.append(line)

    return "".join(result)


def inject(content: str, language: str) -> str:
    """Inject error-catching wrapper into file content.

    If markers already exist, strips and re-injects. For Swift,
    injects after imports and adds a call to the setup function.
    For Python, prepends the wrapper at the top of the file.
    """
    # Strip existing markers first
    clean = strip_markers(content, language)

    if language == "swift":
        return _inject_swift(clean)
    if language == "python":
        return _inject_python(clean)
    return content


def _inject_swift(content: str) -> str:
    """Inject Swift crash handlers after imports."""
    lines = content.splitlines(keepends=True)

    # Find last import line
    last_import = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("import "):
            last_import = i

    # Insert wrapper after imports
    insert_pos = last_import + 1 if last_import >= 0 else 0
    wrapper_lines = SWIFT_WRAPPER.splitlines(keepends=True)
    # Add blank line before wrapper if not at start
    if insert_pos > 0:
        wrapper_lines = ["\n"] + wrapper_lines
    wrapper_lines.append("\n")

    result = lines[:insert_pos] + wrapper_lines + lines[insert_pos:]

    # Add setup call to init() if @main struct exists
    text = "".join(result)
    if re.search(r"@main\b", text):
        text = _add_swift_init_call(text)

    return text


def _add_swift_init_call(content: str) -> str:
    """Add _mcloopSetupCrashHandlers() call to the app's init()."""
    call = "_mcloopSetupCrashHandlers()"
    if call in content:
        return content

    # Look for init() inside a struct/class after @main
    lines = content.splitlines(keepends=True)
    result = []
    in_main_struct = False
    found_init = False

    for i, line in enumerate(lines):
        result.append(line)
        stripped = line.strip()

        if "@main" in stripped:
            in_main_struct = True
            continue

        if in_main_struct and not found_init:
            if "init()" in stripped and "{" in stripped:
                found_init = True
                # Find the opening brace and insert after it
                indent = len(line) - len(line.lstrip()) + 8
                result.append(" " * indent + call + "\n")
                continue

    return "".join(result)


def _inject_python(content: str) -> str:
    """Inject Python crash handlers at the top of the file."""
    # Insert after shebang and encoding lines if present
    lines = content.splitlines(keepends=True)
    insert_pos = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if i == 0 and stripped.startswith("#!"):
            insert_pos = 1
            continue
        if i <= 1 and stripped.startswith("# -*- coding"):
            insert_pos = i + 1
            continue
        break

    wrapper_lines = PYTHON_WRAPPER.splitlines(keepends=True)
    wrapper_lines.append("\n")

    return "".join(lines[:insert_pos]) + "".join(wrapper_lines) + "".join(lines[insert_pos:])


def save_canonical_wrappers(project_dir: Path, language: str) -> None:
    """Save canonical wrapper source to .mcloop/wrap/ for re-injection."""
    wrap_dir = project_dir / ".mcloop" / "wrap"
    wrap_dir.mkdir(parents=True, exist_ok=True)

    if language == "swift":
        (wrap_dir / "swift_wrapper.swift").write_text(SWIFT_WRAPPER)
    elif language == "python":
        (wrap_dir / "python_wrapper.py").write_text(PYTHON_WRAPPER)


def wrap_project(project_dir: Path) -> tuple[str, Path | None]:
    """Instrument the project's entry point with error-catching hooks.

    Returns (language, entry_point) or raises ValueError if language
    cannot be detected or entry point cannot be found.
    """
    language = detect_language(project_dir)
    if language is None:
        raise ValueError(
            "Could not detect project language. "
            "Supports Swift and Python. "
            "Add a PLAN.md description or check file extensions."
        )

    entry = find_entry_point(project_dir, language)
    if entry is None:
        raise ValueError(
            f"Could not find {language} entry point. "
            f"Expected @main struct (Swift) or __main__.py/main.py (Python)."
        )

    content = entry.read_text()
    instrumented = inject(content, language)
    entry.write_text(instrumented)
    save_canonical_wrappers(project_dir, language)

    return (language, entry)
