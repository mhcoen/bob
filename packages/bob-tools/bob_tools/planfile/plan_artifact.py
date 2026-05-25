"""Plan-artifact sanitizer for LLM-emitted reauthor responses.

Duplo's reauthor pipeline drives an LLM that emits a two-part response:
the new PLAN.md body, followed by a single trailing fenced ``json``
block carrying the council verdict. The text adapter captures the
entire response into the ``plan`` artifact, so the verdict text appears
INSIDE the plan artifact even though the orchestra runtime also
surfaces a parsed verdict via the ``judge_verdict`` artifact. This
module reconciles the two representations BEFORE the plan body is
handed to :func:`bob_tools.planfile.parse_plan`, so structural
corruption (an embedded verdict block) does not propagate into PLAN.md.

The helpers live alongside the planfile parser/renderer because they
gate the input to that parser: a plan artifact that violates the
trailing-fenced-verdict contract must be rejected before any planfile
operation runs on it. Keeping the sanitizer in the planfile package
avoids re-introducing the duplo-private parser module that Phase C is
retiring.

Two public names:

  - :func:`sanitize_plan_artifact` — split-and-extract a trailing
    fenced verdict JSON, or reject. Returns ``(plan_text, verdict)``
    where ``verdict`` is ``None`` when no fenced verdict block is
    present.
  - :class:`PlanArtifactRejected` — raised on contract violations
    (mid-body verdict block, multiple verdict blocks). Callers
    translate this into a named pause reason
    (mcloop ``plan_artifact_contained_verdict_json``).
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCED_JSON_BLOCK_RE = re.compile(
    r"^```json\s*\n(?P<body>.*?)\n```\s*$",
    re.MULTILINE | re.DOTALL,
)

_VERDICT_SHAPE_KEYS: frozenset[str] = frozenset({"decision", "lineage"})


class PlanArtifactRejected(ValueError):
    """Raised when :func:`sanitize_plan_artifact` finds a verdict-shaped
    fenced JSON block in a shape that is not the documented
    trailing-fenced-verdict contract.

    The synthesizer's contract is to emit the verdict in the
    ``judge_verdict`` artifact, plus exactly one trailing fenced
    ``json`` block at the end of the plan artifact. Anything else
    (mid-body verdict, multiple verdict blocks) is a model error.
    Silently stripping the verdict would mask it; parsing as-is would
    corrupt PLAN.md. The reauthor caller translates this into a
    named pause reason rather than continuing.
    """


def sanitize_plan_artifact(
    text: str,
) -> tuple[str, dict[str, Any] | None]:
    """Split-and-extract a trailing fenced verdict JSON, or reject.

    The reauthor synthesizer template instructs the model to emit
    its response in two parts: the plan body (markdown) followed by
    a single fenced ``json`` code block carrying the verdict. The
    text adapter captures the entire response into the plan
    artifact, so the verdict text appears INSIDE the plan artifact
    even though orchestra also surfaces a parsed verdict via the
    judge_verdict artifact. This sanitizer reconciles the two
    representations:

      - When the plan artifact ends in a single trailing fenced
        ``json`` block decoding to a verdict-shaped object, return
        ``(plan_text_without_fence, extracted_verdict)``. Caller
        reconciles the extracted verdict against orchestra's
        judge_verdict artifact (they should match; mismatch is a
        named error worth surfacing).
      - When the plan artifact has no fenced ``json`` block (or
        none of them is verdict-shaped), return ``(text, None)``.
      - When a verdict-shaped fenced block sits MID-BODY (not
        trailing), raise :class:`PlanArtifactRejected`. This is the
        original failure mode: a verdict block embedded inside the
        plan body would corrupt PLAN.md across reauthor passes.
      - When MULTIPLE verdict-shaped fenced blocks are present,
        raise :class:`PlanArtifactRejected`. Ambiguous: which one
        is the "trailing" verdict?

    Other fenced code blocks (Python, bash, JSON without verdict
    shape) are passed through untouched.

    Returns
    -------
    tuple[str, dict | None]
        ``(plan_text, extracted_verdict)``. The verdict is None
        when no extraction occurred; otherwise it is the decoded
        JSON object from the trailing fenced block.

    Raises
    ------
    PlanArtifactRejected
        When the plan artifact's verdict-shaped JSON shape is not
        the documented trailing-fenced-verdict shape.
    """
    verdict_blocks: list[tuple[re.Match[str], dict[str, Any]]] = []
    for match in _FENCED_JSON_BLOCK_RE.finditer(text):
        body = match.group("body")
        try:
            decoded = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(decoded, dict):
            continue
        if _VERDICT_SHAPE_KEYS & decoded.keys():
            verdict_blocks.append((match, decoded))

    if not verdict_blocks:
        return (text, None)

    if len(verdict_blocks) > 1:
        positions = ", ".join(
            f"line ~{text.count(chr(10), 0, m.start()) + 1}" for m, _ in verdict_blocks
        )
        raise PlanArtifactRejected(
            "plan artifact contains "
            f"{len(verdict_blocks)} fenced 'json' blocks decoding to "
            f"verdict-shaped objects (at {positions}); the documented "
            "synthesizer contract is exactly ONE trailing fenced "
            "verdict block, not multiple"
        )

    match, decoded = verdict_blocks[0]
    trailing = text[match.end() :]
    if trailing.strip():
        raise PlanArtifactRejected(
            "plan artifact contains a verdict-shaped fenced 'json' "
            f"block at offset {match.start()} that is NOT the "
            "trailing element of the response; non-whitespace "
            f"content follows it ({trailing.lstrip()[:80]!r}). The "
            "documented synthesizer contract requires the verdict "
            "fence at the END of the response"
        )

    plan_text_without_fence = text[: match.start()].rstrip() + "\n"
    return (plan_text_without_fence, decoded)


__all__ = [
    "PlanArtifactRejected",
    "sanitize_plan_artifact",
]
