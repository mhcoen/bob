"""Extract code examples from documentation HTML as input/expected_output pairs."""

from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup, Tag

# Labels that indicate input/request content (checked case-insensitively).
_INPUT_LABELS = re.compile(
    r"\b(input|request|usage|example|command|query|code|sample|syntax)\b",
    re.IGNORECASE,
)

# Labels that indicate output/response content (checked case-insensitively).
_OUTPUT_LABELS = re.compile(
    r"\b(output|response|result|returns?|expected|produces?|prints?|yields?)\b",
    re.IGNORECASE,
)

# Python doctest prompt pattern.
_DOCTEST_RE = re.compile(r"^(>>>|\.\.\.) ", re.MULTILINE)


@dataclass
class CodeExample:
    input: str
    expected_output: str
    source_url: str
    language: str


def extract_code_examples(html: str, source_url: str = "") -> list[CodeExample]:
    """Extract code examples from documentation *html*.

    Looks for code blocks (``<pre>``, ``<code>``) and pairs them as
    input/expected_output using three strategies:

    1. **Labeled pairs** – consecutive code blocks where the preceding
       heading or text labels one as input and the next as output.
    2. **Doctest style** – a single block containing ``>>>`` prompts,
       split into prompt lines (input) and result lines (output).
    3. **Shell style** – a single block where lines starting with ``$``
       or ``%`` are commands (input) and following lines are output.

    Returns a list of :class:`CodeExample` objects.
    """
    soup = BeautifulSoup(html, "lxml")
    blocks = _find_code_blocks(soup)

    examples: list[CodeExample] = []

    # Strategy 1: labeled consecutive pairs.
    used_indices: set[int] = set()
    for i, (code, lang, label) in enumerate(blocks):
        if i in used_indices:
            continue
        if _INPUT_LABELS.search(label) and i + 1 < len(blocks):
            next_code, next_lang, next_label = blocks[i + 1]
            if _OUTPUT_LABELS.search(next_label) or not _INPUT_LABELS.search(next_label):
                examples.append(
                    CodeExample(
                        input=code,
                        expected_output=next_code,
                        source_url=source_url,
                        language=lang or next_lang,
                    )
                )
                used_indices.add(i)
                used_indices.add(i + 1)

    # Strategy 2 & 3: single-block patterns.
    for i, (code, lang, _label) in enumerate(blocks):
        if i in used_indices:
            continue

        doctest_examples = _parse_doctest(code, source_url, lang)
        if doctest_examples:
            examples.extend(doctest_examples)
            used_indices.add(i)
            continue

        example = _parse_shell(code, source_url, lang)
        if example:
            examples.append(example)
            used_indices.add(i)

    return examples


def _find_code_blocks(soup: BeautifulSoup) -> list[tuple[str, str, str]]:
    """Return ``(code_text, language, preceding_label)`` for each block."""
    blocks: list[tuple[str, str, str]] = []
    seen_elements: set[int] = set()

    for pre in soup.find_all("pre"):
        elem_id = id(pre)
        if elem_id in seen_elements:
            continue
        seen_elements.add(elem_id)

        code_tag = pre.find("code")
        if code_tag and isinstance(code_tag, Tag):
            text = code_tag.get_text()
            lang = _detect_language(code_tag)
            seen_elements.add(id(code_tag))
        else:
            text = pre.get_text()
            lang = _detect_language(pre)

        text = text.strip()
        if not text:
            continue

        label = _get_preceding_text(pre)
        blocks.append((text, lang, label))

    # Standalone <code> blocks not inside <pre>.
    for code_tag in soup.find_all("code"):
        if id(code_tag) in seen_elements:
            continue
        if code_tag.find_parent("pre"):
            continue
        text = code_tag.get_text().strip()
        # Skip short inline code spans.
        if not text or "\n" not in text:
            continue
        lang = _detect_language(code_tag)
        label = _get_preceding_text(code_tag)
        blocks.append((text, lang, label))

    return blocks


def _detect_language(tag: Tag) -> str:
    """Detect language from a tag's class attribute (e.g. ``language-python``)."""
    classes = tag.get("class", [])
    if isinstance(classes, str):
        classes = classes.split()
    for cls in classes:
        if cls.startswith("language-"):
            return cls[len("language-") :]
        if cls.startswith("lang-"):
            return cls[len("lang-") :]
    return ""


def _get_preceding_text(tag: Tag) -> str:
    """Collect text from the heading or paragraph immediately before *tag*."""
    parts: list[str] = []
    for sibling in tag.previous_siblings:
        if isinstance(sibling, Tag):
            if sibling.name in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "dt", "th"}:
                parts.append(sibling.get_text(separator=" ").strip())
            break
        text = str(sibling).strip()
        if text:
            parts.append(text)
    # Also check the parent for wrapper divs with headings.
    parent = tag.parent
    if parent and isinstance(parent, Tag) and parent.name == "div":
        heading = parent.find(re.compile(r"^h[1-6]$"))
        if heading and isinstance(heading, Tag):
            parts.append(heading.get_text(separator=" ").strip())
    return " ".join(parts)


def _parse_doctest(code: str, source_url: str, lang: str) -> list[CodeExample]:
    """Parse Python doctest-style code (``>>>`` prompts)."""
    if not _DOCTEST_RE.search(code):
        return []

    results: list[CodeExample] = []
    input_lines: list[str] = []
    output_lines: list[str] = []

    def _flush() -> None:
        if input_lines and output_lines:
            results.append(
                CodeExample(
                    input="\n".join(input_lines),
                    expected_output="\n".join(output_lines),
                    source_url=source_url,
                    language=lang or "python",
                )
            )
        input_lines.clear()
        output_lines.clear()

    for line in code.splitlines():
        if line.startswith(">>> ") or line.startswith("... "):
            if output_lines:
                _flush()
            input_lines.append(line[4:])
        elif line.startswith(">>>"):
            if output_lines:
                _flush()
            input_lines.append(line[3:])
        elif input_lines:
            output_lines.append(line)

    _flush()
    return results


def _parse_shell(code: str, source_url: str, lang: str) -> CodeExample | None:
    """Parse shell-style code (``$`` or ``%`` prompts)."""
    lines = code.splitlines()
    input_lines: list[str] = []
    output_lines: list[str] = []
    in_output = False

    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("$ ") or stripped.startswith("% "):
            if in_output and input_lines:
                # New command after output — keep accumulating.
                in_output = False
            input_lines.append(stripped[2:])
            in_output = False
        elif input_lines and not in_output:
            in_output = True
            output_lines.append(line)
        elif in_output:
            output_lines.append(line)

    if not input_lines or not output_lines:
        return None

    return CodeExample(
        input="\n".join(input_lines),
        expected_output="\n".join(output_lines),
        source_url=source_url,
        language=lang or "shell",
    )
