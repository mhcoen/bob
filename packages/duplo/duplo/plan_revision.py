"""Generate and review a stopped-build plan revision without editing the live plan."""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from bob_tools.json_state import StateError, atomic_write_json, read_json_object
from bob_tools.planfile import PlanValidationError, load, render_plan, save
from bob_tools.planfile.milestones import MILESTONE_INSTRUCTIONS
from mcloop.completion import pending_receipts, project_owner as mcloop_owner
from mcloop.plan_revision import _snapshot, prepare_revision_owned
from mcloop.task_review import _git, _safe_path, load_policy
from orchestra.config import RoleBinding

from duplo.bounded_review import _invoke, parse_object
from duplo.revision_calls import RevisionSession
from duplo.design_plan import _actors, _validate_review
from duplo.file_ops import project_owner
from duplo.revision_edits import EDIT_INSTRUCTIONS, apply_edits, plan_hash
from duplo.software_design import SoftwareDesignError, _digest, _read_state, require_design

DEFAULT_OBJECTIVE = (
    "Establish an early executable application scaffold using completed work, then "
    "extend it through integration milestones in every remaining phase. Replace named "
    "simulations when their real implementations become available."
)
CHECKS = {
    "objective": True,
    "dependencies": True,
    "accepted_contracts": True,
    "scope_preserved": True,
    "executable_integration": True,
}
REVIEW_INSTRUCTIONS = (
    "Review the proposed task operations against the original plan, accepted design "
    "and revision objective. Task status DONE identifies existing implementation; do "
    "not infer missing code merely because a phase still has pending tests. Check "
    "prerequisite order, full retained scope and connected application behavior, and "
    "that named simulations are replaced at the stated points. Do not demand executed "
    "tests for this unimplemented proposal or reapprove the accepted design. Return "
    "only JSON with decision (accept or reject), checks (every supplied ID mapped to "
    "a boolean), and feedback (specific task IDs and corrections). Accept only if all "
    "required checks pass. This is a sequencing review, not implementation acceptance. "
    "Only the supplied design sections and decision choices are included; do not "
    "invent omitted contracts or recommend changing them."
)


def _context(root, plan, design, objective):
    # Choices contain the operative contracts. Alternatives and rationale stay in
    # the accepted design record; the prompt explicitly describes this selection.
    selected = {
        key: design[key]
        for key in ("purpose", "architecture", "data_lifecycle", "failure_behavior", "evolution")
    }
    selected["decisions"] = [
        {key: item[key] for key in ("id", "title", "choice")} for item in design["decisions"]
    ]
    completions = []
    for path in sorted((root / ".mcloop/completions").glob("*.json")):
        data = read_json_object(path)
        completions.append(
            {
                "id": data.get("id"),
                "stage": data.get("stage"),
                "task_ids": [task["id"] for task in data.get("tasks", [])],
                "check_command": data.get("check_command"),
            }
        )
    return {
        "objective": objective,
        "base_sha256": plan_hash(plan),
        "original_plan": render_plan(plan),
        "specification": (root / "SPEC.md").read_text(),
        "accepted_design": selected,
        "context_scope": "Selected design sections and all decision choices; "
        "rationale, alternatives, consequences, full source and raw test logs are not supplied. "
        "Completion receipts report previous execution; they do not establish correctness.",
        "source_paths": [
            name
            for name in _git(root, "ls-files").splitlines()
            if name.startswith(("Sources/", "src/", "Tests/", "tests/"))
        ],
        "completion_records": completions,
    }


def _review_invoker(policy):
    def invoke(root, role, prompt, timeout):
        if role.adapter != "openrouter":
            return _invoke(root, role, prompt, timeout)
        payload = {
            "model": role.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 3000,
            "reasoning": {"effort": "low"},
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + os.environ[policy.api_key_env],
            },
        )
        with urllib.request.urlopen(request, timeout=min(timeout, 90)) as stream:
            raw = stream.read(100_001)
        if len(raw) > 100_000:
            raise SoftwareDesignError("OpenRouter response exceeds 100000 bytes")
        body = json.loads(raw)
        log = root / f"openrouter-{uuid4().hex}.json"
        atomic_write_json(log, body)
        choice = body["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise SoftwareDesignError(f"Incomplete OpenRouter response; see {log}")
        print("OpenRouter usage: " + json.dumps(body.get("usage", {})), flush=True)
        return choice["message"]["content"], log.name

    return invoke


def _call(session, stage, role, instructions, context):
    prompt = instructions + "\n\n" + json.dumps(context, ensure_ascii=False)
    for saved in reversed(session.current["calls"]):
        if saved["stage"] == stage and saved["prompt_digest"] == _digest(prompt):
            if saved["status"] == "complete":
                print(f"Reusing saved {stage} response", flush=True)
                return saved["output"]
    return session.call(stage, role, instructions, context)


def _review(session, stage, reviewer, context):
    instructions = REVIEW_INSTRUCTIONS
    for attempt in range(2):
        slot = stage if attempt == 0 else stage + "-format-retry"
        output = _call(session, slot, reviewer, instructions, context)
        try:
            review = parse_object(output)
            try:
                _validate_review(review, CHECKS)
            except SoftwareDesignError as exc:
                if "Malformed whole-plan review" in str(exc):
                    raise
            return review
        except SoftwareDesignError as exc:
            instructions = REVIEW_INSTRUCTIONS + (
                " Previous response had invalid JSON or review fields: "
                + str(exc)
                + ". Return exactly decision, checks and feedback with every required check."
            )
    raise SoftwareDesignError("GLM returned malformed review data twice; responses are preserved")


def _fresh(root, snapshot, original):
    if (root / "PLAN.md").read_text() != original or _snapshot(root) != snapshot:
        raise SoftwareDesignError(
            "Project changed during revision. The active plan was preserved. "
            "Run duplo revise-plan --new-attempt at the new stopped checkpoint."
        )


def generate_revision(
    root, *, objective=DEFAULT_OBJECTIVE, new_attempt=False, max_input_bytes=200_000, timeout=300
):
    root = Path(root).resolve()
    if not objective.strip() or not 1 <= max_input_bytes <= 256_000 or not 1 <= timeout <= 900:
        raise SoftwareDesignError("Supply an objective, input budget 1..256000 and timeout 1..900")
    with project_owner(root), mcloop_owner(root):
        if pending_receipts(root):
            raise StateError("Run mcloop recover before revising unresolved completions")
        records = _read_state(root)["attempts"]
        if not records:
            raise SoftwareDesignError("An accepted design is required; run duplo design first")
        record = require_design(root, records[-1]["inputs"])
        original = (root / "PLAN.md").read_text()
        plan = load(root / "PLAN.md")
        snapshot = _snapshot(root)
        author, _, _ = _actors(root)
        policy = load_policy(root)
        if (
            not policy.enabled
            or not policy.model.startswith("z-ai/glm")
            or (policy.base_url.rstrip("/") != "https://openrouter.ai/api/v1")
        ):
            raise SoftwareDesignError(
                "Configure task_review with a GLM model and https://openrouter.ai/api/v1"
            )
        reviewer = RoleBinding(adapter="openrouter", model=policy.model)
        limits = {
            "max_calls": 4,
            "max_prompt_bytes": max_input_bytes,
            "max_total_prompt_bytes": max_input_bytes * 4,
            "max_output_bytes": 40_000,
            "timeout_seconds": timeout,
        }
        identity = _digest(
            {
                "snapshot": snapshot,
                "plan": original,
                "objective": objective,
                "author": asdict(author),
                "reviewer": policy.model,
                "limits": limits,
                "design": record["design_digest"],
                "implementation": Path(__file__).read_text()
                + Path(__file__).with_name("revision_edits.py").read_text(),
            }
        )
        directory = _safe_path(root, ".mcloop/revision-authoring")
        directory.mkdir(parents=True, exist_ok=True)
        latest = read_json_object(directory / "latest.json")
        if latest and not new_attempt:
            if latest["identity"] != identity:
                raise SoftwareDesignError(
                    "Revision inputs changed. Run duplo revise-plan --new-attempt; "
                    "previous proposals are retained."
                )
            attempt = _safe_path(root, latest["path"])
        else:
            attempt = directory / uuid4().hex
            attempt.mkdir()
            atomic_write_json(attempt / ".duplo/review-limits.json", limits)
            atomic_write_json(
                directory / "latest.json",
                {"identity": identity, "path": str(attempt.relative_to(root))},
            )
        print(f"Revision work: {attempt}", flush=True)
        state_path = attempt / "revision.json"
        state = read_json_object(state_path) or {"status": "authoring"}
        if state.get("receipt"):
            _fresh(root, snapshot, original)
            staged = read_json_object(Path(state["receipt"]))
            if (
                not staged
                or staged.get("snapshot") != snapshot
                or staged.get("original") != original
            ):
                raise SoftwareDesignError(
                    "Saved proposal is missing or stale; preserve its evidence"
                )
            print(f"Reviewed proposal: {state['receipt']}", flush=True)
            return Path(state["receipt"])
        context = _context(root, plan, record["design"], objective)
        atomic_write_json(attempt / "context.json", context)
        session = RevisionSession(attempt, invoke=_review_invoker(policy))
        feedback = ""
        previous = None
        for round_number in range(2):
            instructions = "Propose a stopped-build plan revision. " + EDIT_INSTRUCTIONS
            instructions += MILESTONE_INSTRUCTIONS
            author_context = {**context, "feedback": feedback, "previous_proposal": previous}
            output = _call(session, f"author-{round_number}", author, instructions, author_context)
            previous = output
            try:
                edits = parse_object(output)
                candidate = apply_edits(plan, edits)
            except (ValueError, StateError, PlanValidationError, TypeError, KeyError) as exc:
                feedback = "Structural validation failed: " + str(exc)
                atomic_write_json(attempt / f"validation-{round_number}.json", {"error": feedback})
                continue
            save(attempt / f"candidate-{round_number}.md", candidate)
            _fresh(root, snapshot, original)
            review = _review(
                session,
                f"review-{round_number}",
                reviewer,
                {**context, "proposed_edits": edits, "checks": CHECKS},
            )
            atomic_write_json(attempt / f"review-{round_number}.json", review)
            try:
                _validate_review(review, CHECKS)
            except SoftwareDesignError as exc:
                feedback = json.dumps(review) + "\n" + str(exc)
                continue
            _fresh(root, snapshot, original)
            require_design(root, record["inputs"])
            state.setdefault("stage_id", uuid4().hex)
            atomic_write_json(state_path, state)
            receipt = prepare_revision_owned(
                root / "PLAN.md",
                attempt / f"candidate-{round_number}.md",
                revision_id=state["stage_id"],
            )
            # Keep provenance beside the staged candidate, where apply consumes it.
            atomic_write_json(
                receipt.parent / "generation.json",
                {
                    "objective": objective,
                    "rationale": edits["rationale"],
                    "authoring_record": str(attempt),
                    "review": review,
                    "calls": session.current["calls"],
                },
            )
            with (receipt.parent / "REVIEW.md").open("a") as stream:
                stream.write(
                    "\nGenerated by Duplo from the stopped project checkpoint.\n\n"
                    + edits["rationale"]
                    + "\n\n"
                    + f"Generation and GLM review: {attempt}\n"
                )
            state.update(status="accepted", receipt=str(receipt))
            atomic_write_json(state_path, state)
            print(
                f"Reviewed proposal: {receipt.parent / 'REVIEW.md'}\n"
                f"Apply: mcloop revise-plan --apply {receipt}\nPLAN.md is unchanged.",
                flush=True,
            )
            return receipt
        atomic_write_json(state_path, {"status": "blocked", "feedback": feedback})
        raise SoftwareDesignError(
            f"Revision needs further work after its correction allowance. See {attempt}. "
            "Use --new-attempt to authorize another bounded attempt."
        )
