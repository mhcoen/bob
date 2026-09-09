"""Preserve review coverage when tests produce many structured reports."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
import test_task_review

from mcloop.review_encoding import ReportEncoding, unpack_changes
from mcloop.review_packets import _verify_coverage, encoded, partition
from mcloop.task_review import review_task


def generated_packet():
    requirements = []
    evidence = {
        "D": {
            "reference": "SPEC.md:1-1",
            "text": "Preserve each observation and reject failed checks.",
        }
    }
    changes = {}
    for index in range(8):
        name = f"Sources/component{index}.py"
        identity = f"R{index}"
        body = f"# component {index}\n" + "# preserve the specified outcome\n" * 60
        changes[name] = body
        evidence[identity] = {
            "reference": f"{name}:1-61",
            "changed_file": name,
            "start_line": 1,
            "end_line": 61,
        }
        requirements.append(
            {
                "requirement_id": f"Q{index + 1}",
                "requirement": f"Check component {index} and its observed failures.",
                "design": [{"evidence_id": "D", "reference": "SPEC.md:1-1"}],
                "implementation": [{"evidence_id": identity, "reference": f"{name}:1-61"}],
                "verification": [{"evidence_id": identity, "reference": f"{name}:1-61"}],
            }
        )
    for index in range(400):
        directory = (
            f"evidence/acceptance/capture-path-12345678-0123-4567-8901-123456789012/{index}"
        )
        changes[directory + "/result.json"] = json.dumps(
            {
                "case": index,
                "status": "failed" if index == 397 else "passed",
                "scope": "Fixture observation with the OS and filesystem running. " * 6,
                "observations": [{"pid": index, "frames": index * 20, "published": False}],
                "diagnostic": "" if index != 397 else "Required assertion failed.",
            },
            indent=2,
        )
        changes[directory + "/stderr"] = ""
    return {
        "task": "Verify eight components and all generated observations.",
        "baseline": "a" * 40,
        "requirements": requirements,
        "evidence": evidence,
        "changed_files": changes,
    }


def test_many_reports_retain_every_file_and_failure():
    packet = generated_packet()
    original = copy.deepcopy(packet)
    parts = partition(packet, 32_000)
    assert 1 < len(parts) <= 4
    assert all(len(encoded(part)) <= 32_000 for part in parts)
    _verify_coverage(packet, parts)
    assert packet == original
    assert {q["requirement_id"] for p in parts for q in p["requirements"]} == {
        f"Q{i}" for i in range(1, 9)
    }
    assert all(p["evidence"]["D"] == packet["evidence"]["D"] for p in parts)
    files = {name: value for p in parts for name, value in unpack_changes(p).items()}
    assert files.keys() == packet["changed_files"].keys()
    for name, content in files.items():
        if name.endswith(".json"):
            assert content["value"] == json.loads(packet["changed_files"][name])
        else:
            assert content == packet["changed_files"][name]
    failed = [
        v["value"]
        for n, v in files.items()
        if n.endswith(".json") and v["value"]["status"] == "failed"
    ]
    assert len(failed) == 1 and failed[0]["case"] == 397
    assert failed[0]["diagnostic"] == "Required assertion failed."


def test_report_encoding_preserves_numbers_markers_and_ambiguous_json():
    report = (
        '{"J1":[{"$text":"T1"}],"$number":"literal",'
        '"number":0.1234567890123456789012345,"large":1e999,"unicode":"é"}'
    )
    changes = {
        "evidence/run/report.json": report,
        "evidence/run/duplicate.json": '{"result":1,"result":2}',
        "evidence/run/invalid.json": '{"result":NaN}',
        "evidence/run/empty.stderr": "",
        "Sources/config.json": report,
    }
    encoder = ReportEncoding(changes)
    tree, encoding = encoder.pack(list(changes))
    expanded = unpack_changes({"changed_files": tree, "report_encoding": encoding})
    assert expanded.pop("evidence/run/report.json")["value"] == json.loads(
        report, parse_float=Decimal
    )
    assert expanded == {k: v for k, v in changes.items() if k != "evidence/run/report.json"}


def test_partition_rejects_a_context_that_cannot_fit():
    packet = generated_packet()
    packet["evidence"]["D"]["text"] = "design\n" * 6000
    with pytest.raises(ValueError, match="cannot fit"):
        partition(packet, 32_000)


@pytest.mark.parametrize("reverse", [False, True])
def test_path_tree_preserves_file_directory_transitions(reverse):
    changes = {
        "evidence/old": {"format": "unified_diff", "patch": "deleted old report"},
        "evidence/old/new.json": '{"passed":false}',
    }
    if reverse:
        changes = dict(reversed(list(changes.items())))
    encoder = ReportEncoding(changes)
    tree, encoding = encoder.pack(list(changes))
    expanded = unpack_changes({"changed_files": tree, "report_encoding": encoding})
    assert expanded["evidence/old"] == changes["evidence/old"]
    assert expanded["evidence/old/new.json"]["value"] == {"passed": False}


def test_coverage_guard_rejects_missing_changes():
    packet = generated_packet()
    parts = partition(packet, 32_000)
    parts[0]["changed_files"] = {}
    with pytest.raises(ValueError, match="omitted"):
        _verify_coverage(packet, parts)


@pytest.mark.parametrize("reject_last", [False, True])
def test_final_review_requires_acceptance_from_every_part(tmp_path: Path, reject_last):
    root, policy, baseline = test_task_review.project.__wrapped__(tmp_path)
    packet = generated_packet()
    policy = replace(policy, max_input_bytes=32_000)

    def approve(_, part):
        rejected = reject_last and part["review_scope"]["part"] == part["review_scope"]["parts"]
        return json.dumps(
            {
                "verdict": "reject" if rejected else "accept",
                "findings": ["A supplied observation failed."] if rejected else [],
                "requirements": [
                    {
                        "requirement_id": q["requirement_id"],
                        "satisfied": not rejected,
                        "reason": "The supplied declarations support this scoped obligation.",
                        "evidence": ["D"],
                    }
                    for q in part["requirements"]
                ],
            }
        )

    with (
        patch("mcloop.task_review.build_packet", return_value=packet),
        patch("mcloop.task_review._request_review", side_effect=approve),
    ):
        result = review_task(root, policy, packet["task"], baseline, "editor")
    assert result.passed is not reject_last
    receipt = json.loads(Path(result.receipt).read_text())
    if not reject_last:
        assert len(receipt["review"]["requirements"]) == 8
    else:
        assert receipt["status"] == "rejected"
        assert receipt["review"]["findings"] == ["A supplied observation failed."]
    assert receipt["part_count"] > 1
    assert len(receipt["part_reviews"]) == receipt["part_count"]
