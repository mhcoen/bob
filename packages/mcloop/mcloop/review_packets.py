"""Partition review changes while retaining cited context and complete coverage."""

from __future__ import annotations

import copy
import hashlib
import json
from decimal import Decimal

from mcloop.recovery_policy import RECOVERY
from mcloop.review_encoding import ReportEncoding, unpack_changes

MAX_PARTS = RECOVERY.review_parts


def encoded(packet: dict) -> bytes:
    return json.dumps(packet, ensure_ascii=False, separators=(",", ":")).encode()


class OversizedPacket(ValueError):
    def __init__(self, message: str, packet: dict):
        super().__init__(message)
        self.packet = packet


def _context(packet: dict) -> dict:
    base = copy.deepcopy(packet)
    changes = base.pop("changed_files")
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
        "changed_file_count": len(changes),
        "changes_sha256": hashlib.sha256(encoded(changes)).hexdigest(),
        "part": MAX_PARTS,
        "parts": MAX_PARTS,
    }
    return base


def _whole_requirements(base: dict, changes: dict, limit: int) -> list[dict]:
    base = dict(base, changed_files={})
    if len(encoded(base)) >= limit:
        return []
    parts = []
    current = copy.deepcopy(base)
    for name, content in changes.items():
        current["changed_files"][name] = content
        if len(encoded(current)) <= limit:
            continue
        del current["changed_files"][name]
        if current["changed_files"]:
            parts.append(current)
        if len(parts) >= MAX_PARTS:
            return []
        current = copy.deepcopy(base)
        current["changed_files"][name] = content
        if len(encoded(current)) > limit:
            return []
    if current["changed_files"]:
        parts.append(current)
    for index, part in enumerate(parts, 1):
        part["review_scope"].update(part=index, parts=len(parts))
    return parts


def _by_requirement(packet: dict, base: dict, limit: int) -> list[dict]:
    reports = ReportEncoding(packet["changed_files"])
    requirements = packet["requirements"]
    design_ids = {
        ref["evidence_id"] for requirement in requirements for ref in requirement["design"]
    }
    manifest = [
        {key: requirement[key] for key in ("requirement_id", "requirement")}
        for requirement in requirements
    ]
    common = {key: value for key, value in base.items() if key not in {"requirements", "evidence"}}

    def assemble(group, names, index, count):
        identities = design_ids | {
            ref["evidence_id"]
            for requirement in group
            for field in ("design", "implementation", "verification")
            for ref in requirement[field]
        }
        evidence = {key: value for key, value in base["evidence"].items() if key in identities}
        tree, encoding = reports.pack(names)
        scope = dict(
            base["review_scope"],
            part=index + 1,
            parts=count,
            all_requirements=manifest,
            instruction=(
                "Assess the requirements array in this part, using its complete cited "
                "passages. all_requirements identifies the task obligations assigned "
                "across the review. Check that this inventory covers the task. Also "
                "review every changed file in this part for defects and conflicts with "
                "the supplied governing design. Other parts assess the remaining "
                "requirements and changes; all parts must accept. Reject if a judgment "
                "needs absent context. Never assume an unseen dependency is correct."
            ),
        )
        return dict(
            common,
            requirements=group,
            evidence=evidence,
            review_scope=scope,
            changed_files=tree,
            report_encoding=encoding,
        )

    ordered_names = sorted(
        packet["changed_files"], key=lambda name: (-len(encoded(reports.changes[name])), name)
    )
    for count in range(2, MAX_PARTS + 1):
        groups = [requirements[index::count] or requirements for index in range(count)]
        names: list[list[str]] = [[] for _ in groups]
        parts = [assemble(group, [], index, count) for index, group in enumerate(groups)]
        sizes = [len(encoded(part)) for part in parts]
        if max(sizes) > limit:
            continue
        cited = [
            {
                ref["reference"].rsplit(":", 1)[0]
                for requirement in group
                for field in ("implementation", "verification")
                for ref in requirement[field]
            }
            for group in groups
        ]
        for name in ordered_names:
            candidates = sorted(
                range(count), key=lambda index: (name not in cited[index], sizes[index])
            )
            for index in candidates:
                candidate = assemble(groups[index], names[index] + [name], index, count)
                size = len(encoded(candidate))
                if size <= limit:
                    names[index].append(name)
                    parts[index], sizes[index] = candidate, size
                    break
            else:
                break
        else:
            _verify_coverage(packet, parts)
            return parts
    raise ValueError(
        f"Complete requirement contexts and changes cannot fit in {MAX_PARTS} review parts. "
        "Use narrower evidence references or a smaller task; completed stages are preserved"
    )


def _verify_coverage(packet: dict, parts: list[dict]) -> None:
    expected = {item["requirement_id"]: item for item in packet["requirements"]}
    seen = set()
    changes = {}
    for part in parts:
        for item in part["requirements"]:
            identity = item["requirement_id"]
            if item != expected.get(identity):
                raise ValueError("Partition changed a requirement")
            seen.add(identity)
            for field in ("design", "implementation", "verification"):
                for ref in item[field]:
                    identity = ref["evidence_id"]
                    if part["evidence"][identity] != _context_evidence(packet, identity):
                        raise ValueError("Partition changed a cited passage")
        expanded = unpack_changes(part)
        if changes.keys() & expanded.keys():
            raise ValueError("Partition duplicated a changed file")
        changes.update(expanded)
    if seen != expected.keys() or changes.keys() != packet["changed_files"].keys():
        raise ValueError("Partition omitted requirements or changed files")
    for name, content in changes.items():
        original = packet["changed_files"][name]
        if isinstance(content, dict) and content.get("format") == "json_value":
            if content["value"] != json.loads(original, parse_float=Decimal):
                raise ValueError("Partition changed a generated observation")
        elif content != original:
            raise ValueError("Partition changed file content")


def _context_evidence(packet, identity):
    original = packet["evidence"][identity]
    if "changed_file" not in original:
        return original
    value = dict(original)
    name = value.pop("changed_file")
    start, end = value.pop("start_line"), value.pop("end_line")
    value["text"] = "\n".join(packet["changed_files"][name].splitlines()[start - 1 : end])
    return value


def partition(packet: dict, limit: int) -> list[dict]:
    if len(encoded(packet)) <= limit:
        return [packet]
    base = _context(packet)
    parts = _whole_requirements(base, packet["changed_files"], limit)
    return parts or _by_requirement(packet, base, limit)
