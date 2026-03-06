"""Generate unit test files from extracted documentation examples."""

from __future__ import annotations

import json
import re
from pathlib import Path

from duplo.doc_examples import CodeExample


def load_code_examples(target_dir: Path | str = ".") -> list[CodeExample]:
    """Load code examples from ``duplo.json`` in *target_dir*.

    Returns an empty list if the file or key is missing.
    """
    path = (Path(target_dir) / ".duplo/duplo.json").resolve()
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data.get("code_examples", [])
    return [
        CodeExample(
            input=ex["input"],
            expected_output=ex["expected_output"],
            source_url=ex.get("source_url", ""),
            language=ex.get("language", ""),
        )
        for ex in raw
    ]


def _sanitize_name(text: str) -> str:
    """Turn arbitrary text into a valid Python identifier fragment."""
    name = re.sub(r"[^a-zA-Z0-9_]+", "_", text)
    name = name.strip("_")[:60]
    return name or "example"


def _category_class_name(source_url: str) -> str:
    """Derive a test class name from a documentation *source_url*.

    ``https://docs.example.com/api/convert`` → ``TestDocsApiConvert``
    """
    if not source_url:
        return "TestUncategorized"
    # Strip scheme and trailing slash.
    path = re.sub(r"^https?://", "", source_url).rstrip("/")
    # Use host + path segments, drop common prefixes.
    parts = re.split(r"[/.\-]+", path)
    # Filter out empty, very short, and generic segments.
    filtered = [
        p for p in parts if len(p) > 1 and p not in {"www", "com", "org", "io", "html", "htm"}
    ]
    if not filtered:
        return "TestUncategorized"
    camel = "".join(word.capitalize() for word in filtered)
    return f"Test{_sanitize_name(camel)}"


def _group_by_source(
    examples: list[CodeExample],
) -> list[tuple[str, list[tuple[int, CodeExample]]]]:
    """Group examples by source_url, preserving original indices.

    Returns ``(class_name, [(original_index, example), ...])`` pairs
    in the order the first example of each group was seen.
    """
    from collections import OrderedDict

    groups: OrderedDict[str, list[tuple[int, CodeExample]]] = OrderedDict()
    for idx, ex in enumerate(examples):
        key = _category_class_name(ex.source_url)
        groups.setdefault(key, []).append((idx, ex))
    return list(groups.items())


def _make_test_id(index: int, example: CodeExample) -> str:
    """Generate a test function name from an example."""
    first_line = example.input.split("\n", 1)[0].strip()
    slug = _sanitize_name(first_line)
    return f"test_doc_example_{index:03d}_{slug}"


def generate_test_source(
    examples: list[CodeExample],
    *,
    project_name: str = "",
) -> str:
    """Generate Python test file source from *examples*.

    Each example becomes a test case that compares ``input`` against
    ``expected_output``.  The tests are structured so McLoop (or a
    developer) can fill in the actual function calls — by default each
    test stores the input and expected output as string literals and
    asserts equality via a placeholder ``run_example()`` helper.

    Args:
        examples: Code examples extracted from documentation.
        project_name: Name of the project (used in module docstring).

    Returns:
        Python source code for a test file.
    """
    if not examples:
        return ""

    header = f'"""Unit tests generated from documentation examples for {project_name or "the project"}."""\n'

    lines = [
        header,
        "",
        "",
        "# -- Helper ---------------------------------------------------------",
        "# Replace this with actual imports and logic from the project.",
        "# Each test calls run_example(input_text) and compares the result",
        "# to the expected output from the documentation.",
        "",
        "",
        "def run_example(input_text: str) -> str:",
        '    """Run an input through the project\'s core logic.',
        "",
        "    TODO: Replace this stub with actual project imports and calls.",
        '    """',
        "    raise NotImplementedError(",
        '        "Replace run_example() with actual project logic"',
        "    )",
        "",
        "",
        "# -- Tests ----------------------------------------------------------",
    ]

    groups = _group_by_source(examples)

    for class_name, group in groups:
        lines.append("")
        lines.append("")
        lines.append(f"class {class_name}:")
        for idx, ex in group:
            func_name = _make_test_id(idx, ex)
            source_comment = f"        # Source: {ex.source_url}" if ex.source_url else ""
            lang_comment = f"        # Language: {ex.language}" if ex.language else ""

            input_repr = repr(ex.input)
            output_repr = repr(ex.expected_output)

            lines.append("")
            lines.append(f"    def {func_name}(self):")
            if source_comment:
                lines.append(source_comment)
            if lang_comment:
                lines.append(lang_comment)
            lines.append(f"        input_text = {input_repr}")
            lines.append(f"        expected = {output_repr}")
            lines.append("        result = run_example(input_text)")
            lines.append("        assert result == expected")

    lines.append("")
    return "\n".join(lines)


def generate_parametrized_test_source(
    examples: list[CodeExample],
    *,
    project_name: str = "",
) -> str:
    """Generate a pytest-parametrized test file from *examples*.

    More compact than :func:`generate_test_source` — all examples
    become rows in a single ``@pytest.mark.parametrize`` call.

    Args:
        examples: Code examples extracted from documentation.
        project_name: Name of the project (used in module docstring).

    Returns:
        Python source code for a test file.
    """
    if not examples:
        return ""

    header = f'"""Unit tests generated from documentation examples for {project_name or "the project"}."""\n'

    lines = [
        header,
        "",
        "import pytest",
        "",
        "",
        "# -- Helper ---------------------------------------------------------",
        "# Replace this with actual imports and logic from the project.",
        "",
        "",
        "def run_example(input_text: str) -> str:",
        '    """Run an input through the project\'s core logic.',
        "",
        "    TODO: Replace this stub with actual project imports and calls.",
        '    """',
        "    raise NotImplementedError(",
        '        "Replace run_example() with actual project logic"',
        "    )",
        "",
        "",
        "# -- Tests ----------------------------------------------------------",
    ]

    groups = _group_by_source(examples)

    for class_name, group in groups:
        var_name = f"{class_name.upper()}_EXAMPLES"
        lines.append("")
        lines.append("")
        lines.append(f"{var_name} = [")
        for _idx, ex in group:
            lines.append("    (")
            lines.append(f"        {repr(ex.input)},")
            lines.append(f"        {repr(ex.expected_output)},")
            lines.append("    ),")
        lines.append("]")
        lines.append("")
        lines.append("")
        lines.append(f"class {class_name}:")
        lines.append(f'    @pytest.mark.parametrize("input_text, expected", {var_name})')
        lines.append("    def test_doc_example(self, input_text: str, expected: str):")
        lines.append("        result = run_example(input_text)")
        lines.append("        assert result == expected")

    lines.append("")
    return "\n".join(lines)


def save_test_file(
    content: str,
    *,
    filename: str = "test_doc_examples_generated.py",
    target_dir: Path | str = ".",
) -> Path:
    """Write generated test content to a file in *target_dir*.

    Returns the path to the written file.
    """
    path = (Path(target_dir) / filename).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def generate_plan_test_tasks(examples: list[CodeExample]) -> list[str]:
    """Return PLAN.md checklist items for wiring up doc-example tests.

    These tasks guide McLoop to replace the ``run_example()`` stub with
    actual project logic so the generated tests pass.
    """
    if not examples:
        return []

    languages = {ex.language for ex in examples if ex.language}
    lang_note = f" ({', '.join(sorted(languages))})" if languages else ""

    return [
        f"- [ ] Wire up {len(examples)} documentation-example test(s){lang_note}",
        "  - [ ] Replace `run_example()` stub in test_doc_examples_generated.py "
        "with actual project imports",
        "  - [ ] Run pytest and fix any failing doc-example tests",
    ]
