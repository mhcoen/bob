"""Tests for mcloop.wrap — source file instrumentation."""

from mcloop.wrap import (
    PYTHON_BEGIN,
    PYTHON_END,
    SWIFT_BEGIN,
    SWIFT_END,
    detect_language,
    find_entry_point,
    has_markers,
    inject,
    save_canonical_wrappers,
    strip_markers,
    wrap_project,
)

# ---- detect_language ----


def test_detect_from_plan_swift(tmp_path):
    (tmp_path / "PLAN.md").write_text("# My Swift App\nA SwiftUI menu bar app.\n- [ ] task\n")
    assert detect_language(tmp_path) == "swift"


def test_detect_from_plan_python(tmp_path):
    plan = "# CLI Tool\nA Python CLI for data processing.\n- [ ] task\n"
    (tmp_path / "PLAN.md").write_text(plan)
    assert detect_language(tmp_path) == "python"


def test_detect_from_extensions_swift(tmp_path):
    src = tmp_path / "Sources"
    src.mkdir()
    (src / "App.swift").write_text("struct App {}")
    (src / "Model.swift").write_text("struct Model {}")
    assert detect_language(tmp_path) == "swift"


def test_detect_from_extensions_python(tmp_path):
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "main.py").write_text("print('hi')")
    assert detect_language(tmp_path) == "python"


def test_detect_from_build_system_swift(tmp_path):
    (tmp_path / "Package.swift").write_text("// swift-tools-version: 5.9\n")
    assert detect_language(tmp_path) == "swift"


def test_detect_from_build_system_python(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    assert detect_language(tmp_path) == "python"


def test_detect_unknown(tmp_path):
    (tmp_path / "README.md").write_text("Hello")
    assert detect_language(tmp_path) is None


def test_detect_plan_takes_priority_over_extensions(tmp_path):
    """PLAN.md description wins even if file extensions disagree."""
    (tmp_path / "PLAN.md").write_text("# My Swift App\n- [ ] task\n")
    pkg = tmp_path / "scripts"
    pkg.mkdir()
    (pkg / "helper.py").write_text("")
    assert detect_language(tmp_path) == "swift"


def test_detect_skips_hidden_dirs(tmp_path):
    hidden = tmp_path / ".build"
    hidden.mkdir()
    (hidden / "main.swift").write_text("")
    (tmp_path / "pyproject.toml").write_text("[project]")
    assert detect_language(tmp_path) == "python"


# ---- find_entry_point ----


def test_find_swift_entry_main_attr(tmp_path):
    src = tmp_path / "Sources"
    src.mkdir()
    (src / "MyApp.swift").write_text("@main\nstruct MyApp: App {}\n")
    assert find_entry_point(tmp_path, "swift") == src / "MyApp.swift"


def test_find_swift_entry_app_suffix(tmp_path):
    src = tmp_path / "Sources"
    src.mkdir()
    (src / "FooApp.swift").write_text("struct FooApp {}\n")
    assert find_entry_point(tmp_path, "swift") == src / "FooApp.swift"


def test_find_swift_entry_main_swift(tmp_path):
    src = tmp_path / "Sources"
    src.mkdir()
    (src / "main.swift").write_text("print('hi')\n")
    (src / "Helpers.swift").write_text("func help() {}\n")
    assert find_entry_point(tmp_path, "swift") == src / "main.swift"


def test_find_python_entry_dunder_main(tmp_path):
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "__main__.py").write_text("print('hi')")
    assert find_entry_point(tmp_path, "python") == pkg / "__main__.py"


def test_find_python_entry_root_main(tmp_path):
    (tmp_path / "main.py").write_text("print('hi')")
    assert find_entry_point(tmp_path, "python") == tmp_path / "main.py"


def test_find_python_entry_nested_main(tmp_path):
    sub = tmp_path / "src" / "app"
    sub.mkdir(parents=True)
    (sub / "main.py").write_text("print('hi')")
    assert find_entry_point(tmp_path, "python") == sub / "main.py"


def test_find_entry_no_match(tmp_path):
    assert find_entry_point(tmp_path, "swift") is None
    assert find_entry_point(tmp_path, "python") is None


# ---- has_markers / strip_markers ----


def test_has_markers_swift():
    text = f"import X\n{SWIFT_BEGIN}\ncode\n{SWIFT_END}\nrest\n"
    assert has_markers(text, "swift") is True
    assert has_markers("import X\nrest\n", "swift") is False


def test_has_markers_python():
    text = f"{PYTHON_BEGIN}\ncode\n{PYTHON_END}\nrest\n"
    assert has_markers(text, "python") is True
    assert has_markers("rest\n", "python") is False


def test_strip_markers_swift():
    text = f"import A\n{SWIFT_BEGIN}\ninjected\n{SWIFT_END}\nstruct B {{}}\n"
    stripped = strip_markers(text, "swift")
    assert SWIFT_BEGIN not in stripped
    assert SWIFT_END not in stripped
    assert "import A\n" in stripped
    assert "struct B {}\n" in stripped
    assert "injected" not in stripped


def test_strip_markers_python():
    text = f"#!/usr/bin/env python\n{PYTHON_BEGIN}\ninjected\n{PYTHON_END}\nx = 1\n"
    stripped = strip_markers(text, "python")
    assert PYTHON_BEGIN not in stripped
    assert "#!/usr/bin/env python\n" in stripped
    assert "x = 1\n" in stripped


def test_strip_no_markers():
    text = "hello\nworld\n"
    assert strip_markers(text, "swift") == text


def test_strip_unsupported_language():
    text = "hello\nworld\n"
    assert strip_markers(text, "rust") == text


# ---- inject ----


def test_inject_swift():
    content = "import SwiftUI\n\n@main\nstruct MyApp: App {\n    init() {\n    }\n}\n"
    result = inject(content, "swift")
    assert SWIFT_BEGIN in result
    assert SWIFT_END in result
    assert "_mcloopSetupCrashHandlers()" in result
    assert "NSSetUncaughtExceptionHandler" in result
    # Signal handlers
    assert "SIGSEGV" in result
    assert "SIGABRT" in result
    assert "SIGBUS" in result
    # State registry
    assert "_McloopState" in result
    assert "snapshot()" in result
    assert "lastAction()" in result
    assert "recordAction" in result
    assert '"last_action"' in result
    assert '"app_state"' in result


def test_inject_swift_no_main():
    content = "import Foundation\n\nfunc doStuff() {}\n"
    result = inject(content, "swift")
    assert SWIFT_BEGIN in result
    assert "_mcloopSetupCrashHandlers" in result
    # No init() call injected since no @main struct
    lines = result.splitlines()
    init_calls = [line for line in lines if "_mcloopSetupCrashHandlers()" in line]
    # Only in the function definition, not as a call site
    assert len(init_calls) == 0 or all(
        "private func" in line or "func " in line for line in init_calls
    )


def test_inject_python():
    content = "#!/usr/bin/env python3\nimport sys\n\ndef main():\n    pass\n"
    result = inject(content, "python")
    assert PYTHON_BEGIN in result
    assert PYTHON_END in result
    assert "_mcloop_setup_crash_handlers()" in result
    # Shebang preserved at top
    assert result.startswith("#!/usr/bin/env python3\n")
    # State registry
    assert "_McloopState" in result
    assert "record_action" in result
    assert "snapshot" in result
    assert "last_action" in result
    assert '"last_action"' in result
    assert '"app_state"' in result
    # Logging integration
    assert "_McloopLogHandler" in result
    assert "logging" in result


def test_inject_python_no_shebang():
    content = "import os\n\nx = 1\n"
    result = inject(content, "python")
    assert PYTHON_BEGIN in result
    assert "import os" in result


def test_inject_idempotent_swift():
    content = "import Foundation\n\nfunc main() {}\n"
    first = inject(content, "swift")
    second = inject(first, "swift")
    # Should only have one set of markers
    assert second.count(SWIFT_BEGIN) == 1
    assert second.count(SWIFT_END) == 1


def test_inject_idempotent_python():
    content = "import sys\n\ndef main():\n    pass\n"
    first = inject(content, "python")
    second = inject(first, "python")
    assert second.count(PYTHON_BEGIN) == 1
    assert second.count(PYTHON_END) == 1


def test_reinject_after_user_edit_swift():
    """After user edits code outside markers, re-inject preserves edits."""
    content = "import SwiftUI\n\n@main\nstruct MyApp: App {\n    init() {\n    }\n}\n"
    injected = inject(content, "swift")
    # Simulate user adding code after the wrapper block
    edited = injected + "\nextension MyApp {\n    func newFeature() {}\n}\n"
    reinjected = inject(edited, "swift")
    assert reinjected.count(SWIFT_BEGIN) == 1
    assert reinjected.count(SWIFT_END) == 1
    assert "newFeature" in reinjected
    assert "NSSetUncaughtExceptionHandler" in reinjected


def test_reinject_after_user_edit_python():
    """After user edits code outside markers, re-inject preserves edits."""
    content = "import sys\n\ndef main():\n    pass\n"
    injected = inject(content, "python")
    # Simulate user adding a new function after injection
    edited = injected + "\ndef new_feature():\n    return 42\n"
    reinjected = inject(edited, "python")
    assert reinjected.count(PYTHON_BEGIN) == 1
    assert reinjected.count(PYTHON_END) == 1
    assert "new_feature" in reinjected
    assert "_mcloop_setup_crash_handlers" in reinjected


def test_reinject_restores_corrupted_wrapper_swift():
    """If user modifies code inside markers, re-inject restores canonical."""
    content = "import Foundation\n\nfunc main() {}\n"
    injected = inject(content, "swift")
    # Corrupt the wrapper by replacing content between markers
    corrupted = injected.replace("_McloopState", "_BrokenState")
    reinjected = inject(corrupted, "swift")
    assert "_McloopState" in reinjected
    assert "_BrokenState" not in reinjected


def test_reinject_restores_corrupted_wrapper_python():
    """If user modifies code inside markers, re-inject restores canonical."""
    content = "import sys\n\ndef main():\n    pass\n"
    injected = inject(content, "python")
    corrupted = injected.replace("_McloopState", "_BrokenState")
    reinjected = inject(corrupted, "python")
    assert "_McloopState" in reinjected
    assert "_BrokenState" not in reinjected


def test_swift_wrapper_state_registry():
    """Swift wrapper includes state registry for @Published property capture."""
    from mcloop.wrap import SWIFT_WRAPPER

    # Registry API
    assert "enum _McloopState" in SWIFT_WRAPPER
    assert "register(" in SWIFT_WRAPPER
    assert "recordAction(" in SWIFT_WRAPPER
    assert "snapshot()" in SWIFT_WRAPPER
    assert "lastAction()" in SWIFT_WRAPPER
    # Thread safety
    assert "NSLock" in SWIFT_WRAPPER
    # Error reports include state and last action
    assert '"app_state": state' in SWIFT_WRAPPER
    assert '"last_action": action' in SWIFT_WRAPPER
    # Reports written to errors.json
    assert "errors.json" in SWIFT_WRAPPER


def test_swift_wrapper_signal_handlers():
    """Swift wrapper installs handlers for SIGSEGV, SIGABRT, SIGBUS."""
    from mcloop.wrap import SWIFT_WRAPPER

    assert "SIGSEGV" in SWIFT_WRAPPER
    assert "SIGABRT" in SWIFT_WRAPPER
    assert "SIGBUS" in SWIFT_WRAPPER
    assert "SIG_DFL" in SWIFT_WRAPPER
    assert "Darwin.raise" in SWIFT_WRAPPER


def test_python_wrapper_state_registry():
    """Python wrapper includes state registry for application state capture."""
    from mcloop.wrap import PYTHON_WRAPPER

    # Registry API
    assert "class _McloopState" in PYTHON_WRAPPER
    assert "register(" in PYTHON_WRAPPER
    assert "record_action(" in PYTHON_WRAPPER
    assert "snapshot()" in PYTHON_WRAPPER
    assert "last_action()" in PYTHON_WRAPPER
    # Error reports include state and last action
    assert '"app_state": state' in PYTHON_WRAPPER
    assert '"last_action":' in PYTHON_WRAPPER
    # Reports written to errors.json
    assert "errors.json" in PYTHON_WRAPPER


def test_python_wrapper_signal_handlers():
    """Python wrapper installs handlers for SIGSEGV and SIGABRT."""
    from mcloop.wrap import PYTHON_WRAPPER

    assert "SIGSEGV" in PYTHON_WRAPPER
    assert "SIGABRT" in PYTHON_WRAPPER
    assert "SIG_DFL" in PYTHON_WRAPPER


def test_python_wrapper_logging_integration():
    """Python wrapper installs a logging handler to capture logged exceptions."""
    from mcloop.wrap import PYTHON_WRAPPER

    assert "_McloopLogHandler" in PYTHON_WRAPPER
    assert "logging.Handler" in PYTHON_WRAPPER
    assert "exc_info" in PYTHON_WRAPPER
    assert "ERROR" in PYTHON_WRAPPER
    assert "addHandler" in PYTHON_WRAPPER


# ---- errors.json format fields ----


_REQUIRED_FIELDS = [
    "timestamp",
    "exception_type",
    "description",
    "stack_trace",
    "source_file",
    "line",
    "app_state",
    "last_action",
    "fix_attempts",
]


def test_swift_wrapper_error_report_fields():
    """Swift exception handler writes all required errors.json fields."""
    from mcloop.wrap import SWIFT_WRAPPER

    for field in _REQUIRED_FIELDS:
        assert f'"{field}"' in SWIFT_WRAPPER, f"missing field: {field}"
    # Signal handler adds signal field
    assert '"signal"' in SWIFT_WRAPPER
    # ID is added by _mcloopWriteError
    assert '"id"' in SWIFT_WRAPPER


def test_python_wrapper_error_report_fields():
    """Python exception hook writes all required errors.json fields."""
    from mcloop.wrap import PYTHON_WRAPPER

    for field in _REQUIRED_FIELDS:
        assert f'"{field}"' in PYTHON_WRAPPER, f"missing field: {field}"
    # Signal handler adds signal field
    assert '"signal"' in PYTHON_WRAPPER
    # ID is added by _write_error
    assert '"id"' in PYTHON_WRAPPER


def test_swift_wrapper_fix_attempts_starts_zero():
    """Swift wrapper initializes fix_attempts to 0."""
    from mcloop.wrap import SWIFT_WRAPPER

    assert '"fix_attempts": 0' in SWIFT_WRAPPER


def test_python_wrapper_fix_attempts_starts_zero():
    """Python wrapper initializes fix_attempts to 0."""
    from mcloop.wrap import PYTHON_WRAPPER

    assert '"fix_attempts": 0' in PYTHON_WRAPPER


def test_python_wrapper_local_variables():
    """Python wrapper captures local variables from the crashing frame."""
    from mcloop.wrap import PYTHON_WRAPPER

    assert "f_locals" in PYTHON_WRAPPER
    assert "tb.tb_next" in PYTHON_WRAPPER
    # Filters out private vars
    assert 'not k.startswith("_")' in PYTHON_WRAPPER


# ---- save_canonical_wrappers ----


def test_save_wrappers_swift(tmp_path):
    save_canonical_wrappers(tmp_path, "swift")
    wrapper = tmp_path / ".mcloop" / "wrap" / "swift_wrapper.swift"
    assert wrapper.exists()
    text = wrapper.read_text()
    assert SWIFT_BEGIN in text
    assert SWIFT_END in text


def test_save_wrappers_python(tmp_path):
    save_canonical_wrappers(tmp_path, "python")
    wrapper = tmp_path / ".mcloop" / "wrap" / "python_wrapper.py"
    assert wrapper.exists()
    text = wrapper.read_text()
    assert PYTHON_BEGIN in text
    assert PYTHON_END in text


def test_save_wrappers_matches_constants(tmp_path):
    """Canonical files contain exact wrapper constants."""
    from mcloop.wrap import PYTHON_WRAPPER, SWIFT_WRAPPER

    save_canonical_wrappers(tmp_path, "swift")
    save_canonical_wrappers(tmp_path, "python")
    swift_file = tmp_path / ".mcloop" / "wrap" / "swift_wrapper.swift"
    python_file = tmp_path / ".mcloop" / "wrap" / "python_wrapper.py"
    assert swift_file.read_text() == SWIFT_WRAPPER
    assert python_file.read_text() == PYTHON_WRAPPER


def test_save_wrappers_overwrites_existing(tmp_path):
    """Re-saving canonical wrappers overwrites previous version."""
    save_canonical_wrappers(tmp_path, "python")
    wrapper = tmp_path / ".mcloop" / "wrap" / "python_wrapper.py"
    wrapper.write_text("corrupted content")
    save_canonical_wrappers(tmp_path, "python")
    assert PYTHON_BEGIN in wrapper.read_text()


# ---- wrap_project (integration) ----


def test_wrap_project_swift(tmp_path):
    (tmp_path / "PLAN.md").write_text("# Swift App\nA SwiftUI app.\n- [ ] task\n")
    src = tmp_path / "Sources"
    src.mkdir()
    entry = src / "MyApp.swift"
    entry.write_text("import SwiftUI\n\n@main\nstruct MyApp: App {\n    init() {\n    }\n}\n")

    lang, path = wrap_project(tmp_path)
    assert lang == "swift"
    assert path == entry
    text = entry.read_text()
    assert SWIFT_BEGIN in text
    assert (tmp_path / ".mcloop" / "wrap" / "swift_wrapper.swift").exists()


def test_wrap_project_python(tmp_path):
    (tmp_path / "PLAN.md").write_text("# CLI\nA Python tool.\n- [ ] task\n")
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    entry = pkg / "__main__.py"
    entry.write_text("import sys\n\ndef main():\n    pass\n")

    lang, path = wrap_project(tmp_path)
    assert lang == "python"
    assert path == entry
    text = entry.read_text()
    assert PYTHON_BEGIN in text
    assert (tmp_path / ".mcloop" / "wrap" / "python_wrapper.py").exists()


def test_wrap_project_no_language(tmp_path):
    (tmp_path / "PLAN.md").write_text("# Misc\n- [ ] task\n")
    import pytest

    with pytest.raises(ValueError, match="Could not detect"):
        wrap_project(tmp_path)


def test_wrap_project_no_entry_point(tmp_path):
    (tmp_path / "PLAN.md").write_text("# Swift App\nA Swift tool.\n- [ ] task\n")
    import pytest

    with pytest.raises(ValueError, match="Could not find"):
        wrap_project(tmp_path)


# ---- CLI integration ----


def test_wrap_subcommand(tmp_path, monkeypatch):
    """Test that the wrap subcommand calls wrap_project."""
    (tmp_path / "PLAN.md").write_text("# Python App\nA Python tool.\n- [ ] task\n")
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "__main__.py").write_text("import sys\n")

    monkeypatch.chdir(tmp_path)
    from mcloop.main import _cmd_wrap

    _cmd_wrap(tmp_path / "PLAN.md")
    assert PYTHON_BEGIN in (pkg / "__main__.py").read_text()
