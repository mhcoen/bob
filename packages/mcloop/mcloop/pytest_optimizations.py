"""Ensure target projects have pytest speed/safety settings.

Two optimizations are critical for mcloop runs:

* ``-n auto`` (pytest-xdist) parallelizes the test suite across cores.
  Without it, a 12-second suite can take 5+ minutes and starve the loop.
* ``timeout`` (pytest-timeout) kills runaway tests before they hang the
  main loop for the full per-task timeout.

This module mutates the target project's ``pyproject.toml`` to add the
``[tool.pytest.ini_options]`` keys and the matching dev dependencies if
they are missing. It is idempotent: subsequent calls are no-ops.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

_DEFAULT_ADDOPTS = "-n auto"
_DEFAULT_TIMEOUT = 60
_XDIST_REQ = "pytest-xdist>=3.5"
_TIMEOUT_REQ = "pytest-timeout>=2.3"
# pytest-cov backs the coverage-proven verification fallback: when an
# unmapped behavioral Python change has no namesake test, the gate runs a
# scoped coverage measurement to prove the changed lines were executed by
# some dependent test. The plugin works with the existing ``-n auto``
# xdist path out of the box -- it combines per-worker coverage data
# automatically and needs no ``parallel=true``/``concurrency``/
# ``sitecustomize``/``COVERAGE_PROCESS_START`` configuration -- so only
# the dev dependency is injected here.
_COV_REQ = "pytest-cov>=4.1"


def ensure_pytest_optimizations(project_dir: Path) -> bool:
    """Ensure pyproject.toml has pytest parallelism + timeout wired up.

    Returns True if the file was modified, False if it was already
    configured or if there is no pyproject.toml.
    """
    pyproject = project_dir / "pyproject.toml"
    if not pyproject.exists():
        return False

    original = pyproject.read_text()
    try:
        data = tomllib.loads(original)
    except tomllib.TOMLDecodeError:
        return False

    content = _ensure_pytest_config(original, data)
    content = _ensure_pytest_deps(content, data)

    if content != original:
        pyproject.write_text(content)
        return True
    return False


def _ensure_pytest_config(content: str, data: dict) -> str:
    pytest_cfg = data.get("tool", {}).get("pytest", {}).get("ini_options")

    if pytest_cfg is None:
        section = (
            "[tool.pytest.ini_options]\n"
            f'addopts = "{_DEFAULT_ADDOPTS}"\n'
            f"timeout = {_DEFAULT_TIMEOUT}\n"
        )
        return content.rstrip() + "\n\n" + section

    addopts = pytest_cfg.get("addopts", "")
    if isinstance(addopts, list):
        addopts_str = " ".join(str(x) for x in addopts)
    else:
        addopts_str = str(addopts)
    has_parallel = _has_parallel_flag(addopts_str)
    has_timeout = "timeout" in pytest_cfg

    insertions: list[str] = []
    if "addopts" not in pytest_cfg:
        insertions.append(f'addopts = "{_DEFAULT_ADDOPTS}"')
    elif not has_parallel:
        content = _extend_addopts(content, _DEFAULT_ADDOPTS)
    if not has_timeout:
        insertions.append(f"timeout = {_DEFAULT_TIMEOUT}")
    if insertions:
        content = _insert_after_header(content, "[tool.pytest.ini_options]", insertions)
    return content


def _has_parallel_flag(addopts: str) -> bool:
    tokens = addopts.split()
    for i, tok in enumerate(tokens):
        if tok == "-n" and i + 1 < len(tokens):
            return True
        if tok.startswith("-n") and len(tok) > 2:
            return True
        if tok.startswith("--numprocesses"):
            return True
    return False


def _extend_addopts(content: str, extra: str) -> str:
    """Append ``extra`` to the addopts value inside [tool.pytest.ini_options]."""
    # Locate the section body and then the addopts line inside it.
    header = "[tool.pytest.ini_options]"
    lines = content.splitlines(keepends=True)
    start = _find_header(lines, header)
    if start is None:
        return content
    end = _find_next_section(lines, start + 1)

    for idx in range(start + 1, end):
        line = lines[idx]
        match = re.match(r'(\s*addopts\s*=\s*)"([^"]*)"(.*)', line)
        if match:
            prefix, value, suffix = match.groups()
            new_value = (value + " " + extra).strip() if value else extra
            replaced = f'{prefix}"{new_value}"{suffix}'
            if not replaced.endswith("\n"):
                replaced += "\n"
            lines[idx] = replaced
            return "".join(lines)
    return content


def _insert_after_header(content: str, header: str, new_lines: list[str]) -> str:
    lines = content.splitlines(keepends=True)
    idx = _find_header(lines, header)
    if idx is None:
        return content
    if not lines[idx].endswith("\n"):
        lines[idx] = lines[idx] + "\n"
    inserted = "".join(line + "\n" for line in new_lines)
    lines.insert(idx + 1, inserted)
    return "".join(lines)


def _find_header(lines: list[str], header: str) -> int | None:
    for i, line in enumerate(lines):
        if line.strip() == header:
            return i
    return None


def _find_next_section(lines: list[str], start: int) -> int:
    for j in range(start, len(lines)):
        stripped = lines[j].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            return j
    return len(lines)


def _ensure_pytest_deps(content: str, data: dict) -> str:
    project = data.get("project", {})
    main_deps = list(project.get("dependencies", []))
    optional = project.get("optional-dependencies", {})
    dev_deps = list(optional.get("dev", []))
    all_deps = main_deps + dev_deps

    to_add: list[str] = []
    if not _dep_present("pytest-xdist", all_deps):
        to_add.append(_XDIST_REQ)
    if not _dep_present("pytest-timeout", all_deps):
        to_add.append(_TIMEOUT_REQ)
    if not _dep_present("pytest-cov", all_deps):
        to_add.append(_COV_REQ)

    if not to_add:
        return content

    has_dev = "dev" in optional
    if has_dev:
        return _append_to_dev_array(content, to_add)

    has_optional_header = "optional-dependencies" in project
    if has_optional_header:
        return _append_new_dev_key(content, to_add)

    return _append_new_optional_section(content, to_add)


def _dep_present(name: str, deps: list) -> bool:
    for dep in deps:
        if _dep_name(str(dep)) == name:
            return True
    return False


def _dep_name(requirement: str) -> str:
    # Strip version specifiers / extras / markers to get the bare name.
    match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
    return match.group(1) if match else ""


def _append_to_dev_array(content: str, items: list[str]) -> str:
    """Insert items into the `dev = [...]` array under [project.optional-dependencies]."""
    header = "[project.optional-dependencies]"
    lines = content.splitlines(keepends=True)
    start = _find_header(lines, header)
    if start is None:
        return content
    end = _find_next_section(lines, start + 1)

    # Find the line containing `dev = [` and then the matching closing `]`.
    dev_start = None
    for i in range(start + 1, end):
        if re.match(r"\s*dev\s*=\s*\[", lines[i]):
            dev_start = i
            break
    if dev_start is None:
        return _append_new_dev_key(content, items)

    # Is it a single-line array?
    first = lines[dev_start]
    if "]" in first[first.index("[") :]:
        # Single-line: dev = ["a", "b"]
        idx = first.rindex("]")
        before = first[:idx].rstrip()
        after = first[idx:]
        # Ensure trailing comma before our items if the list is non-empty.
        needs_comma = not before.rstrip().endswith("[") and not before.rstrip().endswith(",")
        prefix = before + ("," if needs_comma else "") + " "
        addition = ", ".join(f'"{x}"' for x in items)
        lines[dev_start] = prefix + addition + after
        return "".join(lines)

    # Multi-line array: find the closing bracket line.
    close_idx = None
    for j in range(dev_start + 1, end):
        stripped = lines[j].strip()
        if stripped.startswith("]"):
            close_idx = j
            break
    if close_idx is None:
        return content

    # Match indentation from an existing item, or use 4 spaces.
    indent = "    "
    for j in range(dev_start + 1, close_idx):
        m = re.match(r'(\s+)"', lines[j])
        if m:
            indent = m.group(1)
            break

    # Ensure the previous item has a trailing comma.
    if close_idx - 1 > dev_start:
        prev = lines[close_idx - 1].rstrip("\n")
        if prev.strip() and not prev.rstrip().endswith(",") and not prev.rstrip().endswith("["):
            lines[close_idx - 1] = prev + ",\n"

    new_entries = "".join(f'{indent}"{x}",\n' for x in items)
    lines.insert(close_idx, new_entries)
    return "".join(lines)


def _append_new_dev_key(content: str, items: list[str]) -> str:
    """Under existing [project.optional-dependencies], append a `dev = [...]` key."""
    header = "[project.optional-dependencies]"
    lines = content.splitlines(keepends=True)
    start = _find_header(lines, header)
    if start is None:
        return _append_new_optional_section(content, items)
    end = _find_next_section(lines, start + 1)

    block = "dev = [\n" + "".join(f'    "{x}",\n' for x in items) + "]\n"
    insert_at = end
    lines.insert(insert_at, block)
    return "".join(lines)


def _append_new_optional_section(content: str, items: list[str]) -> str:
    section = (
        "[project.optional-dependencies]\n"
        "dev = [\n" + "".join(f'    "{x}",\n' for x in items) + "]\n"
    )
    return content.rstrip() + "\n\n" + section
