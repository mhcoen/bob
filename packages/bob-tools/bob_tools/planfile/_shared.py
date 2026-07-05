"""Shared constants, regexes, and utilities used across planfile operations modules.

Imported by ``construction``, ``iteration``, ``validation``, ``semantic_diff``,
``canonical``, ``scheduling``, ``status``, ``task_addition``, and ``migration``;
does not import from those modules to keep dependency direction acyclic.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from bob_tools.planfile.model import Task

_POSITIONAL_LABEL_RE = re.compile(r"^\d+(?:\.\d+)+$")

# Leading-position bracket form whose content has the shape of an
# operational tag: an all-uppercase identifier of two or more chars,
# optionally followed by ``:word`` (the AUTO action-tag form). Matches
# ``[USER]``, ``[BATCH]``, ``[AUTO:run]``, ``[FOO]``, ``[FOO:bar]``;
# does NOT match lowercase or single-char brackets like ``[x]`` or
# ``[note]`` (those are prose per design doc section 4.3). When the
# parser succeeds at extracting a known leading tag the bracket is
# removed from ``task.text``, so a surviving match here is either a
# tag form the parser does not recognize (``[FOO]``) or a known tag
# in a non-leading-after-other-tags position the parser refused to
# strip — both cases are unknown bracket tags by validation.
_LEADING_TAG_LIKE_RE = re.compile(r"^\[([A-Z][A-Z0-9_]+(?::\w+)?)\]")

# Trailing bracket form. The opening ``[`` must abut either start of
# string or whitespace (mirrors ``_extract_annotations``' separation
# requirement) and the closing ``]`` must be the last non-whitespace
# character. Used to detect malformed annotations: a bracket form the
# parser left in text because its content did not parse as
# ``key: value``.
_TRAILING_BRACKET_RE = re.compile(r"(?:(?<=\s)|^)\[([^\[\]\n]+)\]\s*$")

# Annotation content shape per the parser's ``_ANNOTATION_CONTENT_RE``:
# identifier-shaped key, colon, then mandatory whitespace and value.
# A bracket whose content has a key-and-colon prefix but fails this
# pattern is the canonical "malformed annotation" signal — the author
# intended an annotation but missed the required whitespace or value.
_ANNOTATION_OK_RE = re.compile(r"^[A-Za-z_]\w*:\s+\S.*$", re.DOTALL)

# Annotation-attempt prefix: identifier-shaped key followed immediately
# by a colon. Used to distinguish "this trailing bracket looks like an
# annotation attempt" (``[feat:foo]``) from "this trailing bracket is
# just prose ending in brackets" (``[some text]``); only the former is
# flagged as malformed.
_ANNOTATION_KEY_RE = re.compile(r"^[A-Za-z_]\w*:")

_KNOWN_LEADING_FLAGS = frozenset({"USER", "BATCH"})

_ACCEPT_KINDS = frozenset({"pytest", "command-exit", "coverage", "waived"})

# Bracket forms reserved by the grammar for non-task-tag constructs.
# Per design doc section 4.3 (planfile.md:415-417), ``[RULEDOUT]`` is
# **not** a task tag; it is a sibling line at the child indent under
# the task it pertains to. When the literal token appears at the
# leading position of a task body — e.g. a task whose title describes
# the RULEDOUT feature itself (mcloop/PLAN.EXAMPLE.md:243) — it is
# prose, not an attempted unknown tag. Flagging it would conflate
# "unknown task tag" (a real validation concern) with "task title
# legitimately mentions a reserved keyword" (prose by design).
_RESERVED_SIBLING_MARKERS = frozenset({"RULEDOUT"})

# Canonical task reference: either the legacy ``T-NNNNNN`` form or the
# namespaced ``T-XX-NNNNNN`` form (T-000003). The namespace segment is
# exactly two letters; the digit suffix stays six digits in canonical
# form. ``_TASK_REF_RE`` is used both by deps validation and by the
# field-stability harness, so a regex change here cascades to both.
_TASK_REF_RE = re.compile(r"^T-(?:[A-Za-z]{2}-)?\d{6}$")

_TASK_ID_NUMERIC_RE = re.compile(r"^T-(?:[A-Za-z]{2}-)?(\d+)$")

_ACTION_NAME_RE = re.compile(r"^\w+$")

_ANNOTATION_KEY_ONLY_RE = re.compile(r"^[A-Za-z_]\w*$")

_INCOMPLETE_CHECKBOX_RE = re.compile(r"^\s*- \[ \] .+$", re.MULTILINE)

_PHASE_ID_RE = re.compile(r"^phase_(\d+)$")


def _contains_newline(value: str) -> bool:
    return "\n" in value or "\r" in value


def _count_unfenced_incomplete_checkboxes(text: str) -> int:
    """Count incomplete-checkbox lines outside ``` fences.

    The parser treats content inside a Markdown code fence as verbatim
    (a ``- [ ]`` line in a fenced example is not a task), so any count
    used to cross-check the parser against raw text must apply the same
    fence rule or every fenced example trips a false "task dropped"
    mismatch.
    """
    count = 0
    in_fence = False
    for line in text.splitlines():
        if in_fence or line.lstrip().startswith("```"):
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
            continue
        if _INCOMPLETE_CHECKBOX_RE.match(line):
            count += 1
    return count


def _now_iso_utc() -> str:
    """Return the current UTC instant as an ISO 8601 string.

    Seconds precision and the trailing ``Z`` suffix keep the rendered
    ``<!-- created_at: ... -->`` comment compact and easy to compare
    lexicographically (which sorts chronologically for any same-zone
    timestamps). Mutating operations call this once per added task
    rather than threading a clock argument through every API; tests
    that need a deterministic value monkey-patch this function.
    """
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _task_path_label(path: tuple[int, ...]) -> str:
    if not path:
        return "task"
    return "task.children" + "".join(f"[{index}]" for index in path)


def _task_ref(task: Task) -> str:
    """Return a short reference for ``task`` for use in validator messages.

    Prefers the stable ``T-NNNNNN`` id when present, falling back to the
    1-based source line number for compat-mode tasks (no id). Validation
    messages must locate the offending task uniquely; both forms appear
    elsewhere in the codebase (``T-...`` in deps references, ``line N``
    in mcloop's parser diagnostics), so reusing them keeps the human
    fix-it experience consistent.
    """
    return task.task_id if task.task_id is not None else f"line {task.line_number}"
