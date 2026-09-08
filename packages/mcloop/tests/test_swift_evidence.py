"""Compiler-backed evidence preserves declarations and their enclosing context."""

import shutil
from unittest.mock import patch

import pytest

from mcloop.evidence_refs import resolve
from mcloop.swift_evidence import _declarations


@pytest.fixture(autouse=True)
def clear_cache():
    _declarations.cache_clear()
    yield
    _declarations.cache_clear()


def test_missing_compiler_preserves_whole_file():
    source = "struct Other {}\nstruct Wanted {}\nstruct Tail {}\n"
    with patch("mcloop.swift_evidence.shutil.which", return_value=None):
        assert resolve("file.swift#Wanted", source) == ("file.swift", 1, 3)


@pytest.mark.skipif(not shutil.which("swiftc"), reason="Swift compiler unavailable")
def test_complete_type_with_documentation_attributes_and_raw_string():
    source = """struct Before {}
/// Required contract.
@available(macOS 14, *)
public struct Wanted {
    let value = #"} // not a closing brace"#
    /* nested /* comment */ remains inside */
    func render() -> String { value }
}
struct After {}
"""
    assert resolve("file.swift#Wanted", source) == ("file.swift", 2, 8)
    assert resolve("file.swift#render", source) == ("file.swift", 2, 8)
    assert resolve("file.swift", source) == ("file.swift", 1, 9)


@pytest.mark.skipif(not shutil.which("swiftc"), reason="Swift compiler unavailable")
def test_multiline_extension_keeps_generic_constraints():
    source = """struct Before {}
extension Array
where Element: Equatable {
    func wanted(_ value: Element) -> Bool {
        contains(value)
    }
}
struct After {}
"""
    assert resolve("file.swift#wanted", source) == ("file.swift", 2, 7)


@pytest.mark.skipif(not shutil.which("swiftc"), reason="Swift compiler unavailable")
def test_overload_is_ambiguous():
    with pytest.raises(ValueError, match="one declaration"):
        resolve("file.swift#wanted", "func wanted(_ n: Int) {}\nfunc wanted(_ s: String) {}\n")


@pytest.mark.skipif(not shutil.which("swiftc"), reason="Swift compiler unavailable")
def test_conditional_compilation_keeps_file():
    source = "struct Before {}\n#if os(macOS)\nstruct Wanted {}\n#endif\n"
    assert resolve("file.swift#Wanted", source) == ("file.swift", 1, 4)


@pytest.mark.skipif(not shutil.which("swiftc"), reason="Swift compiler unavailable")
def test_qualified_methods_distinguish_types_and_keep_enclosing_context():
    source = """struct Other {
    func run() {}
}
struct Wanted {
    struct Nested {
        func run() {}
    }
    func run() {}
}
extension Wanted {
    func additional() {}
}
"""
    assert resolve("file.swift#Other.run", source) == ("file.swift", 1, 3)
    assert resolve("file.swift#Wanted.run", source) == ("file.swift", 4, 9)
    assert resolve("file.swift#Wanted.Nested.run", source) == ("file.swift", 4, 9)
    assert resolve("file.swift#Wanted.additional", source) == ("file.swift", 10, 12)
    with pytest.raises(ValueError, match="one declaration"):
        resolve("file.swift#run", source)
    with pytest.raises(ValueError, match="one heading or declaration"):
        resolve("file.swift#Missing.run", source)


@pytest.mark.skipif(not shutil.which("swiftc"), reason="Swift compiler unavailable")
def test_overloads_with_same_enclosing_declaration_include_both_bodies():
    source = "struct Wanted {\nfunc run(_ n: Int) {}\nfunc run(_ s: String) {}\n}\n"
    assert resolve("file.swift#Wanted.run", source) == ("file.swift", 1, 4)
    assert resolve("file.swift#run", source) == ("file.swift", 1, 4)


def test_duplicate_ranges_resolve_without_discarding_any_declaration():
    source = "actor Scheduler {\nfunc admit(_ n: Int) {}\nfunc admit(_ s: String) {}\n}\n"
    with (
        patch("mcloop.swift_evidence.shutil.which", return_value="swiftc"),
        patch(
            "mcloop.swift_evidence._declarations",
            return_value=(
                ("admit", 1, 4),
                ("admit", 1, 4),
            ),
        ),
    ):
        assert resolve("file.swift#admit", source) == ("file.swift", 1, 4)
