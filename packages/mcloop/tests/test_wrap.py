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


# ---- inject ----


def test_inject_swift():
    content = "import SwiftUI\n\n@main\nstruct MyApp: App {\n    init() {\n    }\n}\n"
    result = inject(content, "swift")
    assert SWIFT_BEGIN in result
    assert SWIFT_END in result
    assert "_mcloopSetupCrashHandlers()" in result
    assert "NSSetUncaughtExceptionHandler" in result


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


# ---- save_canonical_wrappers ----


def test_save_wrappers_swift(tmp_path):
    save_canonical_wrappers(tmp_path, "swift")
    wrapper = tmp_path / ".mcloop" / "wrap" / "swift_wrapper.swift"
    assert wrapper.exists()
    assert SWIFT_BEGIN in wrapper.read_text()


def test_save_wrappers_python(tmp_path):
    save_canonical_wrappers(tmp_path, "python")
    wrapper = tmp_path / ".mcloop" / "wrap" / "python_wrapper.py"
    assert wrapper.exists()
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
