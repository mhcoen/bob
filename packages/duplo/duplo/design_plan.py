"""Author and review a complete implementation plan within the review allowance."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from bob_tools.json_state import atomic_write_json, read_json_object
from bob_tools.planfile import render_plan, save, validate_plan
from bob_tools.planfile.model import TaskStatus
from bob_tools.planfile.milestones import MILESTONE_INSTRUCTIONS, validate_milestones
from orchestra.api.dispatch import _resolve_compound_model_identifiers
from orchestra.config import load_config

from duplo.bounded_review import parse_object
from duplo.council import typed_plan_from_synthesizer_text
from duplo.plan_author_role import PLAN_AUTHOR_CRITERIA
from duplo.plan_sanity import check_plan_sanity
from duplo.software_design import (
    SoftwareDesignError,
    _configuration,
    _digest,
    bind_phase_plan,
    require_design,
)


def _actors(root):
    config = load_config(project_dir=root)
    binding = config.role_bindings.get("plan_author")
    if binding:
        roles = _resolve_compound_model_identifiers("plan_author", binding.bindings)
        author, reviewer = roles["proposer"], roles["reviewer"]
        criteria = [asdict(c) for c in (binding.criteria or config.criteria)]
    else:
        design_config, _ = _configuration(root)
        author, reviewer = design_config.roles["author"], design_config.roles["reviewer"]
        criteria = list(PLAN_AUTHOR_CRITERIA)
    if (author.adapter, author.model) == (reviewer.adapter, reviewer.model):
        raise SoftwareDesignError("The plan reviewer must use a different actor.")
    if any(r.adapter not in {"claude_code_text", "codex_text"} for r in (author, reviewer)):
        raise SoftwareDesignError("Whole-plan review requires text adapters.")
    return author, reviewer, criteria


def _candidate(text, record, spec):
    plan = typed_plan_from_synthesizer_text(text, required_phase_id="phase_001")

    def pending(tasks):
        return all(t.status is TaskStatus.TODO and pending(t.children) for t in tasks)

    if any(not pending(p.tasks) for p in plan.phases):
        raise SoftwareDesignError("A new implementation plan must contain only unchecked tasks.")
    expected = [f"phase_{i:03d}" for i in range(1, len(plan.phases) + 1)]
    if [p.phase_id for p in plan.phases] != expected:
        raise SoftwareDesignError("Plan phase IDs must be sequential from phase_001.")
    plan = bind_phase_plan(plan, record, [])
    validate_plan(plan, constructed=True)
    report = check_plan_sanity(render_plan(plan), spec=spec)
    if not report.ok:
        raise SoftwareDesignError(f"Plan requires correction: {report.violations}")
    validate_milestones(plan, required=True)
    return plan


def _validate_review(review, required):
    if (
        set(review) != {"decision", "checks", "feedback"}
        or review["decision"] not in ("accept", "reject")
        or not isinstance(review["checks"], dict)
        or set(review["checks"]) != set(required)
        or any(type(v) is not bool for v in review["checks"].values())
        or not isinstance(review["feedback"], str)
        or not review["feedback"].strip()
    ):
        raise SoftwareDesignError("Malformed whole-plan review.")
    if review["decision"] != "accept" or not all(
        review["checks"][key] for key, mandatory in required.items() if mandatory
    ):
        raise SoftwareDesignError("Implementation plan was not accepted. Findings are preserved.")


def generate_design_plan(root, inputs, record, spec, session):
    require_design(root, inputs)
    path = root / "PLAN.md"
    receipt_path = root / ".duplo/design-plan.json"
    if path.is_symlink() or receipt_path.is_symlink():
        raise SoftwareDesignError("Plan and receipt must be regular files.")
    before = path.read_bytes() if path.exists() else None
    receipt = read_json_object(receipt_path)
    author, reviewer, criteria = _actors(root)
    required = {
        "requirements": True,
        "dependencies": True,
        "interfaces": True,
        "verification": True,
        "scope": True,
        "scaffolded_integration": True,
    }
    for criterion in criteria:
        if criterion["id"] in required:
            raise SoftwareDesignError("Plan criterion duplicates a built-in check.")
        required[criterion["id"]] = criterion.get("required", True)
    policy = _digest({"implementation": Path(__file__).read_text(), "criteria": criteria})
    if receipt is not None:
        if (
            type(receipt.get("schema_version")) is not int
            or receipt.get("schema_version") != 1
            or type(receipt.get("accepted")) is not bool
            or receipt.get("design_digest") != record["design_digest"]
            or receipt.get("policy_digest") != policy
        ):
            raise SoftwareDesignError("Plan receipt belongs to different inputs or review policy.")
        if not isinstance(receipt.get("candidate"), str):
            raise SoftwareDesignError("Plan receipt has no reusable candidate; preserve it.")
        # Parsing a canonical candidate preserves its assigned task identities.
        from bob_tools.planfile import parse_plan, assert_mcloop_canonical

        plan = parse_plan(receipt["candidate"])
        validate_plan(plan)
        assert_mcloop_canonical(plan)
        if receipt.get("candidate_digest") != _digest(render_plan(plan)):
            raise SoftwareDesignError("Plan candidate digest mismatch.")
        if before is not None and before.decode() != receipt["candidate"]:
            raise SoftwareDesignError("PLAN.md contains edits; preserve them before replanning.")
    else:
        if before is not None:
            raise SoftwareDesignError(
                "PLAN.md already exists without a whole-plan receipt. Preserve it before replanning."
            )
        text = session.call(
            "plan-author",
            author,
            "Write the complete implementation phase sequence for the accepted design. "
            "Return only a Markdown plan with ## Phase phase_001: Title headers, numbered "
            "sequentially, and unchecked tasks. Use 5-15 top-level tasks per phase. "
            "Put prerequisites before their consumers. Preserve interfaces and recovery "
            "contracts. Include integration work and failure-path verification with explicit "
            'expected outcomes. Use [feat: "Requirement name"] annotations for feature '
            "tasks and [accept: command-exit: COMMAND] for executable acceptance checks. "
            "Reserve [USER] for checks requiring human participation or an explicit "
            "product decision. Document and test-expectation reviews are ordinary model "
            "tasks unless the accepted criteria explicitly require a human reviewer. "
            "Name the review inputs, report path and conditions for escalating unresolved "
            "disagreements. Do not ask the user to reapprove an accepted design during "
            "implementation. Describe a complete "
            "plan without implementing it. Supplied criteria also apply to each phase. "
            "Return the plan within 50000 characters. " + MILESTONE_INSTRUCTIONS,
            {"inputs": inputs, "design": record["design"], "criteria": criteria},
        )
        plan = _candidate(text, record, spec)
        receipt = {
            "schema_version": 1,
            "design_digest": record["design_digest"],
            "policy_digest": policy,
            "candidate": render_plan(plan),
            "candidate_digest": _digest(render_plan(plan)),
            "accepted": False,
        }
        atomic_write_json(receipt_path, receipt)
    if not check_plan_sanity(render_plan(plan), spec=spec).ok:
        raise SoftwareDesignError("Saved plan fails scope validation.")
    validate_milestones(plan, required=True)
    require_design(root, inputs)
    if not receipt.get("accepted"):
        if receipt.get("review_budget_id") == session.current["id"]:
            raise SoftwareDesignError(
                "This plan's review already used this allowance. Findings are preserved."
            )
        receipt.update(review_budget_id=session.current["id"], review_status="pending")
        atomic_write_json(receipt_path, receipt)
        review = parse_object(
            session.call(
                "plan-review",
                reviewer,
                "Review the complete plan against the accepted design and specification. "
                "Check requirement coverage, prerequisite ordering, cross-phase interfaces, "
                "verification outcomes, and scope. Check that the first milestone demonstrates an "
                "assembled executable path, subsequent milestones extend it, and simulations "
                "are replaced when implementations become available. Compilation alone is "
                "insufficient. Assess whether test expectations describe "
                "the intended behavior. Also assess the configured criteria. Return only JSON "
                "with decision (accept or reject), checks (every supplied check ID mapped to "
                "a boolean), and feedback (a nonempty string explaining findings). "
                "Accept only when all required checks pass. Return within 12000 characters.",
                {
                    "inputs": inputs,
                    "design": record["design"],
                    "plan": render_plan(plan),
                    "checks": required,
                    "criteria": criteria,
                },
            )
        )
        receipt.update(review=review, review_status="complete")
        atomic_write_json(receipt_path, receipt)
        _validate_review(review, required)
        receipt["accepted"] = True
        atomic_write_json(receipt_path, receipt)
    _validate_review(receipt["review"], required)
    require_design(root, inputs)
    if (path.read_bytes() if path.exists() else None) != before:
        raise SoftwareDesignError("PLAN.md changed during review; edits preserved.")
    save(path, plan)
    print(f"Plan ready: {len(plan.phases)} phases in PLAN.md.", flush=True)
