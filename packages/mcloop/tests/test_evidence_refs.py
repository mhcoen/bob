"""Python evidence anchors identify declarations by their lexical scope."""

import pytest

from mcloop.evidence_refs import resolve

SOURCE = """class Other:
    def run(self):
        return False

class OwnershipChecksTests:
    @staticmethod
    async def run():
        return True

    class Nested:
        def run(self):
            return 2

def factory():
    def run():
        return 3
    return run
"""


@pytest.mark.parametrize(
    ("anchor", "span"),
    [
        ("Other.run", (2, 3)),
        ("OwnershipChecksTests.run", (6, 8)),
        ("OwnershipChecksTests.Nested.run", (11, 12)),
        ("factory.run", (15, 16)),
        ("factory", (14, 17)),
    ],
)
def test_qualified_python_anchor_selects_complete_declaration(anchor, span):
    assert resolve("checks.py#" + anchor, SOURCE) == ("checks.py", *span)


@pytest.mark.parametrize("anchor", ["Missing.run", "Nested.run"])
def test_incorrect_scope_is_refused(anchor):
    with pytest.raises(ValueError, match="one declaration"):
        resolve("checks.py#" + anchor, SOURCE)


def test_ambiguous_python_anchor_preserves_every_candidate():
    assert resolve("checks.py#run", SOURCE) == ("checks.py", 1, 17)


def test_duplicate_qualified_declarations_preserve_both_branches():
    source = """class Checks:
    if supported:
        def run(self): pass
    else:
        def run(self): pass
"""
    assert resolve("checks.py#Checks.run", source) == ("checks.py", 1, 5)


def test_ambiguous_heading_preserves_both_sections():
    source = "# First\n## Results\nFailure\n# Second\n## Results\nSuccess\n"
    assert resolve("report.md#Results", source) == ("report.md", 1, 6)


def test_ambiguous_generic_symbol_preserves_both_declarations():
    source = "function snapshot(x) {}\nfunction snapshot(x, y) {}\n"
    assert resolve("file.js#snapshot", source) == ("file.js", 1, 2)
