"""Tests for mcloop.change_class -- the conservative behavioral classifier.

The allowlist cases (comment-only, docstring-only, AST-equivalent
formatting, import reorder with unchanged import graph) must classify as
NON_BEHAVIORAL. Everything else -- renames, ``__all__`` edits, added
imports, decorators, dataclass fields, unparseable source -- must classify
as BEHAVIORAL (fail closed).
"""

import pytest

from mcloop.change_class import (
    ChangeClass,
    classify_change,
    is_no_test_needed_input,
    is_provably_non_behavioral,
)

# --- allowlist cases: provably non-behavioral ---


def test_identical_source_is_non_behavioral():
    src = "x = 1\n"
    assert classify_change(src, src) is ChangeClass.NON_BEHAVIORAL


def test_comment_only_change_is_non_behavioral():
    old = "x = 1  # old comment\ndef f():\n    return x\n"
    new = "x = 1  # a totally different comment\ndef f():\n    return x  # added\n"
    assert classify_change(old, new) is ChangeClass.NON_BEHAVIORAL


def test_module_docstring_only_change_is_non_behavioral():
    old = '"""Old summary."""\n\nx = 1\n'
    new = '"""New, longer summary describing the module."""\n\nx = 1\n'
    assert classify_change(old, new) is ChangeClass.NON_BEHAVIORAL


def test_function_docstring_only_change_is_non_behavioral():
    old = 'def f():\n    """Old."""\n    return 1\n'
    new = 'def f():\n    """New docstring text."""\n    return 1\n'
    assert classify_change(old, new) is ChangeClass.NON_BEHAVIORAL


def test_formatting_only_change_is_non_behavioral():
    old = "def f(a,b):\n    return a+b\n"
    new = "def f(a, b):\n    return a + b\n"
    assert classify_change(old, new) is ChangeClass.NON_BEHAVIORAL


def test_blank_line_and_indent_reflow_is_non_behavioral():
    old = "x = 1\ndef f():\n    return x\n"
    new = "x = 1\n\n\ndef f():\n    return x\n"
    assert classify_change(old, new) is ChangeClass.NON_BEHAVIORAL


def test_import_statement_reorder_is_non_behavioral():
    old = "import os\nimport sys\n\nx = os.getcwd()\n"
    new = "import sys\nimport os\n\nx = os.getcwd()\n"
    assert classify_change(old, new) is ChangeClass.NON_BEHAVIORAL


def test_import_name_reorder_is_non_behavioral():
    old = "from os import getcwd, sep\n"
    new = "from os import sep, getcwd\n"
    assert classify_change(old, new) is ChangeClass.NON_BEHAVIORAL


def test_new_empty_file_against_empty_baseline_is_non_behavioral():
    # A brand-new file with only comments/whitespace carries no behavior.
    assert classify_change("", "# just a comment\n\n") is ChangeClass.NON_BEHAVIORAL


# --- behavioral cases: must fail closed ---


def test_rename_is_behavioral():
    old = "def old_name():\n    return 1\n"
    new = "def new_name():\n    return 1\n"
    assert classify_change(old, new) is ChangeClass.BEHAVIORAL


def test_all_edit_is_behavioral():
    old = '__all__ = ["a"]\n'
    new = '__all__ = ["a", "b"]\n'
    assert classify_change(old, new) is ChangeClass.BEHAVIORAL


def test_added_import_changes_graph_is_behavioral():
    old = "import os\n\nx = 1\n"
    new = "import os\nimport sys\n\nx = 1\n"
    assert classify_change(old, new) is ChangeClass.BEHAVIORAL


def test_decorator_added_is_behavioral():
    old = "def f():\n    return 1\n"
    new = "@staticmethod\ndef f():\n    return 1\n"
    assert classify_change(old, new) is ChangeClass.BEHAVIORAL


def test_dataclass_field_added_is_behavioral():
    old = "class C:\n    a: int = 1\n"
    new = "class C:\n    a: int = 1\n    b: int = 2\n"
    assert classify_change(old, new) is ChangeClass.BEHAVIORAL


def test_value_change_is_behavioral():
    assert classify_change("x = 1\n", "x = 2\n") is ChangeClass.BEHAVIORAL


def test_new_code_file_against_empty_baseline_is_behavioral():
    assert classify_change("", "def f():\n    return 1\n") is ChangeClass.BEHAVIORAL


def test_unparseable_source_fails_closed():
    assert classify_change("def f(:\n", "x = 1\n") is ChangeClass.BEHAVIORAL


def test_is_provably_non_behavioral_predicate():
    assert is_provably_non_behavioral("x = 1\n", "x = 1  # note\n")
    assert not is_provably_non_behavioral("x = 1\n", "x = 2\n")


# --- no-test-needed non-code input class ---


@pytest.mark.parametrize(
    "path",
    [
        "pyproject.toml",  # dependency manifest
        "deep/nested/pyproject.toml",
        "ruff.toml",  # tool config
        "mypy.ini",
        "pytest.ini",
        "setup.cfg",
        "tox.ini",
        ".flake8",  # dotfile config (empty pathlib suffix)
        ".coveragerc",
        ".editorconfig",
        "requirements.txt",  # requirements / lock
        "requirements/dev.txt",
        "poetry.lock",
        "uv.lock",
        "Pipfile",
        "data/fixtures.json",  # plain data / docs
        "config/settings.yaml",
        "table.csv",
        "README.md",
        "docs/guide.rst",
    ],
)
def test_non_code_inputs_need_no_test(path):
    assert is_no_test_needed_input(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "pkg/widget.py",  # executable Python is never exempt
        "tests/test_widget.py",
        "templates/email.j2",  # logic-bearing inputs stay subject to the gate
        "templates/page.html",
        "queries/report.sql",
        "scripts/deploy.sh",
        "Makefile",
        "app/main.js",
    ],
)
def test_code_and_logic_inputs_are_not_exempt(path):
    assert is_no_test_needed_input(path) is False
