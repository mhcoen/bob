"""Partition review changes while retaining cited context and complete coverage."""

from __future__ import annotations

import copy
import hashlib
import json

from mcloop.recovery_policy import RECOVERY

MAX_PARTS = RECOVERY.review_parts


def encoded(packet: dict) -> bytes:
    return json.dumps(packet, ensure_ascii=False, separators=(",", ":")).encode()


class OversizedPacket(ValueError):
    def __init__(self, message: str, packet: dict):
        super().__init__(message)
        self.packet = packet


def partition(packet: dict, limit: int) -> list[dict]:
    if len(encoded(packet)) <= limit:
        return [packet]
    base = copy.deepcopy(packet)
    changes = base.pop("changed_files")
    # Every part receives the complete cited passages. References into complete
    # changed files must become text before distributing those files.
    for excerpt in base["evidence"].values():
        name = excerpt.pop("changed_file", None)
        if name is not None:
            first, last = excerpt.pop("start_line"), excerpt.pop("end_line")
            excerpt["text"] = "\n".join(changes[name].splitlines()[first - 1 : last])
    base["review_scope"] = {
        "instruction": (
            "This is one part of a review. Assess every supplied requirement against "
            "the cited context and the changes in this part. Check the complete task "
            "against the requirement list for omitted obligations. Other changed files "
            "are assessed in separate parts; all parts must accept. Reject if needed "
            "context is absent. Never assume an unseen dependency is correct."
        ),
        "changes": {
            name: hashlib.sha256(encoded({"content": value})).hexdigest()
            for name, value in changes.items()
        },
        "part": MAX_PARTS,
        "parts": MAX_PARTS,
    }
    base["changed_files"] = {}
    if len(encoded(base)) >= limit:
        raise ValueError(
            "Cited review context alone exceeds the per-request budget. "
            "Use narrower references; all completed stages are preserved."
        )
    parts = []
    current = copy.deepcopy(base)
    for name, content in changes.items():
        current["changed_files"][name] = content
        if len(encoded(current)) <= limit:
            continue
        del current["changed_files"][name]
        if current["changed_files"]:
            parts.append(current)
        current = copy.deepcopy(base)
        current["changed_files"][name] = content
        if len(encoded(current)) > limit:
            raise ValueError(f"Changed file and required context exceed review budget: {name}")
    if current["changed_files"]:
        parts.append(current)
    if not parts or len(parts) > MAX_PARTS:
        raise ValueError(f"Review requires more than {MAX_PARTS} parts; narrow the task evidence")
    for index, part in enumerate(parts, 1):
        part["review_scope"].update(part=index, parts=len(parts))
    return parts
