"""Finite design review with durable call reservations and explicit patches."""

from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

import orchestra
from bob_tools.json_state import atomic_write_json, read_json_object
from orchestra.config import OrchestraConfig, WorkflowConfig

from duplo.parsing import extract_json


@dataclass(frozen=True)
class ReviewLimits:
    max_calls: int = 8
    max_prompt_bytes: int = 200_000
    max_total_prompt_bytes: int = 800_000
    max_output_bytes: int = 160_000
    timeout_seconds: int = 600
    max_tokens: int | None = None

    def validate(self):
        from duplo.software_design import SoftwareDesignError

        for key, value in asdict(self).items():
            if key == "max_tokens" and value is None:
                continue
            if type(value) is not int or value < 1:
                raise SoftwareDesignError(f"Review limit {key} must be a positive integer.")
        if self.max_tokens is not None:
            raise SoftwareDesignError(
                "The text CLI adapters cannot enforce a token cap. No model call was made. "
                "Use call and prompt-byte limits, or an adapter with enforced token limits."
            )


def _invoke(root, role, prompt, timeout):
    from duplo.software_design import _deploy

    _deploy(root)
    config = OrchestraConfig(
        roles={"worker": role},
        workflows={"bounded_call": WorkflowConfig(pattern="bounded_call")},
    )
    result = orchestra.run_workflow(
        "bounded_call",
        {"query": prompt},
        config,
        project_dir=root,
        data_root=root / ".duplo/design-runs",
        invocation_options={"timeout": timeout},
    )
    if result.terminal != "done" or "response" not in result.artifacts:
        from duplo.software_design import SoftwareDesignError

        raise SoftwareDesignError(f"Review call failed. Evidence: {result.log_path}")
    return result.artifacts["response"].value, str(result.log_path.relative_to(root))


class ReviewSession:
    """The caller holds project ownership throughout a session."""

    def __init__(self, root: Path, *, reset: bool = False):
        from duplo.software_design import SoftwareDesignError

        self.root = root
        self.path = root / ".duplo/review-budget.json"
        config = read_json_object(root / ".duplo/review-limits.json") or {}
        try:
            self.limits = ReviewLimits(**config)
        except TypeError as exc:
            raise SoftwareDesignError(f"Invalid review limits: {exc}") from exc
        self.limits.validate()
        if self.path.is_symlink():
            raise SoftwareDesignError("Review budget must be a regular file.")
        self.state = read_json_object(self.path)
        if self.state is None:
            self.state = {"schema_version": 1, "sessions": []}
        if (
            type(self.state.get("schema_version")) is not int
            or self.state.get("schema_version") != 1
            or not isinstance(self.state.get("sessions"), list)
        ):
            raise SoftwareDesignError("Malformed review budget; preserve the file.")
        for session in self.state["sessions"]:
            if not isinstance(session, dict) or not isinstance(session.get("calls"), list):
                raise SoftwareDesignError("Malformed review reservations; preserve the file.")
            for call in session["calls"]:
                if (
                    not isinstance(call, dict)
                    or type(call.get("prompt_bytes")) is not int
                    or call["prompt_bytes"] < 0
                ):
                    raise SoftwareDesignError("Malformed review reservation; preserve the file.")
        if reset or not self.state["sessions"]:
            self.state["sessions"].append(
                {"id": uuid4().hex, "limits": asdict(self.limits), "calls": []}
            )
            self.save()
        self.current = self.state["sessions"][-1]
        if self.current.get("limits") != asdict(self.limits):
            raise SoftwareDesignError(
                "Review limits changed. Use --new-review-budget to apply them explicitly."
            )

    def save(self):
        atomic_write_json(self.path, self.state)

    def call(self, stage, role, instructions, context):
        from duplo.software_design import SoftwareDesignError, _digest

        prompt = instructions + "\n\n" + json.dumps(context, ensure_ascii=False)
        # bounded_call.md adds its terminating newline when rendered.
        size = len((prompt + "\n").encode("utf-8"))
        calls = self.current["calls"]
        if len(calls) >= self.limits.max_calls:
            raise SoftwareDesignError("Review call allowance exhausted. Evidence is preserved.")
        if size > self.limits.max_prompt_bytes or (
            sum(c["prompt_bytes"] for c in calls) + size > self.limits.max_total_prompt_bytes
        ):
            raise SoftwareDesignError(
                f"Review prompt ({size} bytes) exceeds its allowance. No call was made."
            )
        receipt = {
            "stage": stage,
            "actor": {"adapter": role.adapter, "model": role.model},
            "prompt_digest": _digest(prompt),
            "prompt_bytes": size,
            "status": "reserved",
        }
        calls.append(receipt)
        self.save()
        print(
            f"Review call {len(calls)}/{self.limits.max_calls}: {stage}, {size} prompt bytes",
            flush=True,
        )
        try:
            output, log_path = _invoke(self.root, role, prompt, self.limits.timeout_seconds)
            receipt.update(output=output, log_path=log_path, output_bytes=len(output.encode()))
            if receipt["output_bytes"] > self.limits.max_output_bytes:
                raise SoftwareDesignError("Review response exceeds the output-size limit.")
            receipt["status"] = "complete"
            self.save()
            return output
        except BaseException as exc:
            receipt.update(status="failed", error=type(exc).__name__ + ": " + str(exc))
            self.save()
            raise


def parse_object(text):
    from duplo.software_design import SoftwareDesignError

    try:
        value = json.loads(extract_json(text))
    except (TypeError, ValueError) as exc:
        raise SoftwareDesignError("Review response must contain a JSON object.") from exc
    if not isinstance(value, dict):
        raise SoftwareDesignError("Review response must be a JSON object.")
    return value


def apply_patch(design, patch, requirements):
    from duplo.software_design import SECTIONS, SoftwareDesignError, _digest, validate_design

    if set(patch) != {"base_digest", "replace", "decisions"}:
        raise SoftwareDesignError("Design patch requires base_digest, replace, and decisions.")
    if patch["base_digest"] != _digest(design):
        raise SoftwareDesignError("Design patch refers to a different candidate digest.")
    if not isinstance(patch["replace"], dict) or not isinstance(patch["decisions"], list):
        raise SoftwareDesignError("Malformed design patch replacements.")
    if not set(patch["replace"]) <= set(SECTIONS) | {"assumptions", "coverage", "open_questions"}:
        raise SoftwareDesignError("Design patch names an unknown section.")
    result = copy.deepcopy(design)
    result.update(patch["replace"])
    ids = {d["id"]: i for i, d in enumerate(result["decisions"])}
    seen = set()
    for decision in patch["decisions"]:
        if not isinstance(decision, dict) or not isinstance(decision.get("id"), str):
            raise SoftwareDesignError("Malformed decision replacement.")
        key = decision["id"]
        if key in seen:
            raise SoftwareDesignError("Duplicate decision in patch.")
        seen.add(key)
        if key in ids:
            result["decisions"][ids[key]] = decision
        else:
            result["decisions"].append(decision)
    validate_design(result, requirements, allow_blocking=True)
    return result


def validate_findings(value, design, previous=()):
    from duplo.software_design import SECTIONS, SoftwareDesignError

    if set(value) != {"findings"} or not isinstance(value["findings"], list):
        raise SoftwareDesignError("Review requires a findings list.")
    targets = set(SECTIONS) | {"assumptions", "coverage", "open_questions"}
    targets.update(d["id"] for d in design["decisions"])
    findings = value["findings"]
    seen = set()
    for finding in findings:
        if not isinstance(finding, dict) or set(finding) != {
            "id",
            "targets",
            "blocking",
            "problem",
            "basis",
        }:
            raise SoftwareDesignError("Malformed review finding.")
        if (
            any(
                not isinstance(finding[k], str) or not finding[k].strip()
                for k in ("id", "problem", "basis")
            )
            or type(finding["blocking"]) is not bool
        ):
            raise SoftwareDesignError("Finding identity, basis, and severity are required.")
        if finding["id"] in seen:
            raise SoftwareDesignError("Duplicate finding identity.")
        seen.add(finding["id"])
        if (
            not isinstance(finding["targets"], list)
            or not finding["targets"]
            or any(not isinstance(t, str) or t not in targets for t in finding["targets"])
        ):
            raise SoftwareDesignError("Finding names an unknown design target.")
    # Earlier findings remain available for adjudication even when omitted on follow-up.
    merged = {f["id"]: dict(f) for f in previous}
    for finding in findings:
        if finding["id"] in merged and finding != merged[finding["id"]]:
            raise SoftwareDesignError("A finding ID cannot be reused for a different objection.")
        merged[finding["id"]] = finding
    return list(merged.values())


def adjudicate(value, findings, previous=()):
    import jsonschema
    from duplo.software_design import SoftwareDesignError, _schema

    if set(value) != {"verdict", "dispositions"}:
        raise SoftwareDesignError("Judgment requires verdict and dispositions.")
    try:
        jsonschema.validate(value["verdict"], _schema("software_design_verdict.json"))
    except jsonschema.ValidationError as exc:
        raise SoftwareDesignError(f"Invalid design judgment: {exc.message}") from exc
    settled = {f["id"] for f in previous if f.get("status") in ("resolved", "dismissed")}
    decisions = value["dispositions"]
    if not isinstance(decisions, list):
        raise SoftwareDesignError("Judgment dispositions must be a list.")
    by_id = {f["id"]: f for f in findings}
    seen = set()
    ledger = []
    for decision in decisions:
        if (
            not isinstance(decision, dict)
            or set(decision) != {"id", "status", "reason"}
            or not isinstance(decision["id"], str)
            or decision["id"] not in by_id
            or decision["id"] in seen
            or decision["status"] not in ("fix", "dismissed", "resolved")
            or not isinstance(decision["reason"], str)
            or not decision["reason"].strip()
        ):
            raise SoftwareDesignError("Malformed or duplicate finding disposition.")
        if decision["id"] in settled and decision["status"] == "fix":
            raise SoftwareDesignError(
                "Reopening a settled finding requires a new evidence finding."
            )
        seen.add(decision["id"])
        ledger.append({**by_id[decision["id"]], **decision})
    if seen != set(by_id):
        raise SoftwareDesignError("Every finding needs a judgment disposition.")
    if value["verdict"]["decision"] == "accept" and any(
        f["blocking"] and f["status"] == "fix" for f in ledger
    ):
        raise SoftwareDesignError("Accepting judgment leaves a blocking finding open.")
    return value["verdict"], ledger


REVIEW = """Review the engineering design against its requirements. Return only an object
with findings: a list of objects with id (stable F001-style string), targets (section
names or decision IDs), blocking (boolean), problem, and basis. Give a concrete
failure scenario, contradiction, or unmet requirement as the basis. Avoid speculative
objections without a consequence. An empty list is permitted. On follow-up, inspect
changed contracts and dependent behavior. Reuse existing IDs only for the same
objection, verbatim. Previously resolved or dismissed findings need new evidence
before reopening; record that evidence as a new finding with a new ID.
Test assertions can encode incorrect expectations. Assess intended behavior itself.
Return findings within 12000 characters."""

JUDGE = """Judge the design and the review findings. Return only an object with verdict
and dispositions. The verdict follows the supplied schema. Each finding needs a
unique disposition with id, status (fix, dismissed, or resolved), and a specific
reason. Dismiss unsupported objections. Require changes for demonstrated defects.
For an earlier resolved or dismissed finding, preserve the disposition unless new
review evidence supports reopening it. Accept only if every required check is true
and every blocking finding has been resolved or dismissed. Budget exhaustion never
justifies acceptance. Return the judgment within 12000 characters."""


def focused_candidate(design, patch, ledger):
    """Include global contracts and decisions connected by explicit references."""
    import re
    from duplo.software_design import SECTIONS

    decisions = {d["id"]: d for d in design["decisions"]}
    selected = {t for f in ledger if f.get("status") == "fix" for t in f["targets"]}
    if patch:
        selected.update(d["id"] for d in patch["decisions"])
    # Global contracts stay present, including references to their decision definitions.
    global_text = json.dumps(
        {k: v for k, v in design.items() if k not in {"decisions", "coverage"}}
    )
    references = {
        key: re.compile(r"(?<![\w-])" + re.escape(key) + r"(?![\w-])") for key in decisions
    }
    selected.update(key for key, pattern in references.items() if pattern.search(global_text))
    # A section-only finding may affect any decision that has no explicit reference.
    if any(t in SECTIONS for t in selected):
        selected.update(decisions)
    changed = True
    while changed:
        before = set(selected)
        for key, decision in decisions.items():
            mentioned = {
                k for k, pattern in references.items() if pattern.search(json.dumps(decision))
            }
            if key in selected or mentioned.intersection(selected):
                selected.add(key)
                selected.update(mentioned)
        changed = selected != before
    result = copy.deepcopy(design)
    result["decisions"] = [d for d in design["decisions"] if d["id"] in selected]
    result["omitted_decisions"] = [
        {"id": d["id"], "title": d["title"]}
        for d in design["decisions"]
        if d["id"] not in selected
    ]
    return result


def review_design(root, inputs, config, prior, session, checkpoint):
    from duplo.software_design import (
        ASSETS,
        SoftwareDesignError,
        _digest,
        _schema,
        validate_design,
    )

    requirements = [f["name"] for f in inputs["requirements"]]
    design = None
    if prior:
        raw = prior.get("proposal") or prior.get("design")
        if raw:
            design = parse_object(raw) if isinstance(raw, str) else copy.deepcopy(raw)
            validate_design(
                design, requirements, allow_blocking=True, allow_incomplete_coverage=True
            )
    if design is None:
        instructions = (
            (ASSETS / "templates/software_design_author.md")
            .read_text()
            .split("Review inputs and proposal schema:")[0]
        )
        design = parse_object(
            session.call(
                "design-author",
                config.roles["author"],
                instructions + "\nReturn the design within 50000 characters.",
                {"inputs": inputs, "schema": _schema("software_design.json")},
            )
        )
        validate_design(design, requirements, allow_blocking=True)
    evidence = {
        "proposal": json.dumps(design),
        "review": "",
        "verdict": {},
        "findings": copy.deepcopy(prior.get("findings", [])) if prior else [],
    }
    checkpoint(evidence)
    ledger = copy.deepcopy(prior.get("findings", [])) if prior else []
    fields = {"id", "targets", "blocking", "problem", "basis"}
    findings = [{k: f[k] for k in fields} for f in ledger]
    if findings:
        validate_findings({"findings": findings}, design)
    patch = None
    for cycle in range(2):
        context = {"inputs": inputs, "candidate": design, "earlier_dispositions": ledger}
        if cycle:
            context.update(candidate=focused_candidate(design, patch, ledger), patch=patch)
        elif prior and not ledger and not prior.get("accepted"):
            context["previous_objections"] = {
                "review": prior.get("review", ""),
                "verdict": prior.get("verdict", {}),
            }
        review = parse_object(
            session.call("design-review", config.roles["reviewer"], REVIEW, context)
        )
        findings = validate_findings(review, design, findings)
        dispositions = {f["id"]: f for f in ledger}
        evidence.update(
            review=json.dumps(review),
            findings=[{**dispositions.get(f["id"], {}), **f} for f in findings],
        )
        checkpoint(evidence)
        judgment = parse_object(
            session.call(
                "design-judge",
                config.roles["judge_role"],
                JUDGE,
                {
                    "inputs": inputs,
                    "candidate": design,
                    "findings": findings,
                    "earlier_dispositions": ledger,
                    "verdict_schema": _schema("software_design_verdict.json"),
                },
            )
        )
        verdict, ledger = adjudicate(judgment, findings, ledger)
        evidence.update(verdict=verdict, findings=ledger)
        checkpoint(evidence)
        if verdict["decision"] == "accept":
            validate_design(design, requirements)
            return evidence
        if cycle or verdict["decision"] == "stuck":
            raise SoftwareDesignError(
                "Software design was not accepted. Review sequence finished."
            )
        if not any(f["status"] == "fix" for f in ledger):
            raise SoftwareDesignError("Software design was not accepted; no actionable findings.")
        patch = parse_object(
            session.call(
                "design-patch",
                config.roles["author"],
                "Correct the adjudicated findings and dependent contracts. Return only a JSON patch "
                "with base_digest, replace (mapping of changed top-level sections except decisions), "
                "and decisions (complete changed or new decision objects). Omit unchanged sections "
                "and decisions. Preserve existing decision IDs. Decisions cannot be deleted. "
                "Keep coverage references valid. Return the patch within 30000 characters.",
                {
                    "inputs": inputs,
                    "candidate": focused_candidate(design, None, ledger),
                    "base_digest": _digest(design),
                    "findings": ledger,
                    "schema": _schema("software_design.json"),
                },
            )
        )
        design = apply_patch(design, patch, requirements)
        evidence.update(proposal=json.dumps(design), patch=patch)
        checkpoint(evidence)
    raise AssertionError("unreachable")
