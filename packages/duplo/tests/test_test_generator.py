"""Tests for duplo.test_generator."""

from __future__ import annotations

import json

from duplo.doc_examples import CodeExample
from duplo.test_generator import (
    generate_parametrized_test_source,
    generate_plan_test_tasks,
    generate_test_source,
    load_code_examples,
    save_test_file,
)


def _make_example(
    input: str = "1 + 2",
    expected_output: str = "3",
    source_url: str = "https://example.com/docs",
    language: str = "python",
) -> CodeExample:
    return CodeExample(
        input=input,
        expected_output=expected_output,
        source_url=source_url,
        language=language,
    )


class TestLoadCodeExamples:
    def test_loads_from_duplo_json(self, tmp_path):
        data = {
            "code_examples": [
                {
                    "input": "print(1)",
                    "expected_output": "1",
                    "source_url": "https://example.com",
                    "language": "python",
                }
            ]
        }
        (tmp_path / "duplo.json").write_text(json.dumps(data))
        examples = load_code_examples(tmp_path)
        assert len(examples) == 1
        assert examples[0].input == "print(1)"
        assert examples[0].expected_output == "1"
        assert examples[0].source_url == "https://example.com"
        assert examples[0].language == "python"

    def test_returns_empty_when_no_file(self, tmp_path):
        assert load_code_examples(tmp_path) == []

    def test_returns_empty_when_no_key(self, tmp_path):
        (tmp_path / "duplo.json").write_text(json.dumps({"features": []}))
        assert load_code_examples(tmp_path) == []

    def test_multiple_examples(self, tmp_path):
        data = {
            "code_examples": [
                {"input": "a", "expected_output": "b"},
                {"input": "c", "expected_output": "d"},
            ]
        }
        (tmp_path / "duplo.json").write_text(json.dumps(data))
        examples = load_code_examples(tmp_path)
        assert len(examples) == 2

    def test_missing_optional_fields(self, tmp_path):
        data = {"code_examples": [{"input": "x", "expected_output": "y"}]}
        (tmp_path / "duplo.json").write_text(json.dumps(data))
        examples = load_code_examples(tmp_path)
        assert examples[0].source_url == ""
        assert examples[0].language == ""


class TestGenerateTestSource:
    def test_empty_returns_empty(self):
        assert generate_test_source([]) == ""

    def test_single_example(self):
        ex = _make_example()
        source = generate_test_source([ex])
        assert "def test_doc_example_000_" in source
        assert "run_example(input_text)" in source
        assert repr(ex.input) in source
        assert repr(ex.expected_output) in source

    def test_source_url_comment(self):
        ex = _make_example(source_url="https://docs.example.com/api")
        source = generate_test_source([ex])
        assert "# Source: https://docs.example.com/api" in source

    def test_language_comment(self):
        ex = _make_example(language="ruby")
        source = generate_test_source([ex])
        assert "# Language: ruby" in source

    def test_no_source_url_no_comment(self):
        ex = _make_example(source_url="")
        source = generate_test_source([ex])
        assert "# Source:" not in source

    def test_project_name_in_docstring(self):
        ex = _make_example()
        source = generate_test_source([ex], project_name="MyApp")
        assert "MyApp" in source

    def test_multiple_examples(self):
        examples = [_make_example(input=f"ex{i}") for i in range(3)]
        source = generate_test_source(examples)
        assert "test_doc_example_000_" in source
        assert "test_doc_example_001_" in source
        assert "test_doc_example_002_" in source

    def test_valid_python_syntax(self):
        ex = _make_example(input='print("hello")', expected_output="hello")
        source = generate_test_source([ex])
        compile(source, "<test>", "exec")

    def test_multiline_input(self):
        ex = _make_example(input="for i in range(3):\n    print(i)")
        source = generate_test_source([ex])
        compile(source, "<test>", "exec")

    def test_special_chars_in_input(self):
        ex = _make_example(input="x = {'a': 1}\nprint(x)")
        source = generate_test_source([ex])
        compile(source, "<test>", "exec")


class TestGenerateParametrizedSource:
    def test_empty_returns_empty(self):
        assert generate_parametrized_test_source([]) == ""

    def test_contains_parametrize(self):
        ex = _make_example()
        source = generate_parametrized_test_source([ex])
        assert "@pytest.mark.parametrize" in source
        assert "import pytest" in source

    def test_single_test_function(self):
        examples = [_make_example(input=f"ex{i}") for i in range(3)]
        source = generate_parametrized_test_source(examples)
        assert source.count("def test_doc_example") == 1

    def test_valid_python_syntax(self):
        ex = _make_example()
        source = generate_parametrized_test_source([ex])
        compile(source, "<test>", "exec")

    def test_project_name_in_docstring(self):
        ex = _make_example()
        source = generate_parametrized_test_source([ex], project_name="Foo")
        assert "Foo" in source


class TestSaveTestFile:
    def test_writes_file(self, tmp_path):
        content = "# test\n"
        path = save_test_file(content, target_dir=tmp_path)
        assert path.exists()
        assert path.read_text() == content

    def test_default_filename(self, tmp_path):
        path = save_test_file("x", target_dir=tmp_path)
        assert path.name == "test_doc_examples_generated.py"

    def test_custom_filename(self, tmp_path):
        path = save_test_file("x", filename="test_custom.py", target_dir=tmp_path)
        assert path.name == "test_custom.py"

    def test_creates_parent_dirs(self, tmp_path):
        subdir = tmp_path / "tests" / "generated"
        path = save_test_file("x", target_dir=subdir)
        assert path.exists()


class TestGeneratePlanTestTasks:
    def test_empty_returns_empty(self):
        assert generate_plan_test_tasks([]) == []

    def test_returns_checklist_items(self):
        examples = [_make_example()]
        tasks = generate_plan_test_tasks(examples)
        assert len(tasks) == 3
        assert "- [ ]" in tasks[0]
        assert "1 documentation-example test(s)" in tasks[0]

    def test_includes_language(self):
        examples = [_make_example(language="python"), _make_example(language="ruby")]
        tasks = generate_plan_test_tasks(examples)
        assert "python" in tasks[0]
        assert "ruby" in tasks[0]

    def test_count_reflects_examples(self):
        examples = [_make_example(input=f"ex{i}") for i in range(5)]
        tasks = generate_plan_test_tasks(examples)
        assert "5 documentation-example test(s)" in tasks[0]
