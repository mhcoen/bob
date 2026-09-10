"""Author and retain a reviewed software design before implementation planning."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import jsonschema
from bob_tools.json_state import StateError, atomic_write_json, read_json_object
from bob_tools.planfile.fileio import _atomic_write_text
from orchestra.api.dispatch import _resolve_compound_model_identifiers
from orchestra.config import OrchestraConfig, RoleBinding, WorkflowConfig, load_config

from duplo.extractor import Feature
from duplo.file_ops import project_owner
from duplo.parsing import extract_json
from duplo.questioner import BuildPreferences

ASSETS = Path(__file__).parent / "workflows"
DESIGN_FILE = "SOFTWARE_DESIGN.md"
STATE_FILE = ".duplo/software-design.json"
SECTIONS = (
    "purpose",
    "architecture",
    "data_lifecycle",
    "failure_behavior",
    "evolution",
    "verification",
)


class SoftwareDesignError(StateError):
    """Design evidence is missing, stale, or unsuitable for planning."""


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _schema(name: str) -> dict:
    result: dict = json.loads((ASSETS / "schemas" / name).read_text())
    return result


def _policy_digest() -> str:
    assets = {
        str(p.relative_to(ASSETS)): p.read_text()
        for pattern in (
            "software_design.orc",
            "bounded_call.orc",
            "templates/bounded_call.md",
            "templates/software_design_*.md",
            "schemas/software_design*.json",
        )
        for p in ASSETS.glob(pattern)
    }
    assets["bounded_review.py"] = (Path(__file__).parent / "bounded_review.py").read_text()
    return _digest(assets)


def validate_design(
    value: dict,
    requirements: list[str],
    *,
    allow_blocking: bool = False,
    allow_incomplete_coverage: bool = False,
) -> None:
    """Check structure and references; the model review assesses the reasoning."""
    try:
        jsonschema.validate(value, _schema("software_design.json"))
    except jsonschema.ValidationError as exc:
        raise SoftwareDesignError(f"Invalid software design: {exc.message}") from exc
    ids = [d["id"] for d in value["decisions"]]
    covered = [c["requirement"] for c in value["coverage"]]
    if len(ids) != len(set(ids)) or len(covered) != len(set(covered)):
        raise SoftwareDesignError("Duplicate decision or requirement identity in software design.")
    if not allow_incomplete_coverage and set(requirements) != set(covered):
        raise SoftwareDesignError(
            "Design coverage must name every input requirement exactly once."
        )
    for row in value["coverage"]:
        if not set(row["decision_ids"]) <= set(ids):
            raise SoftwareDesignError(f"Unknown decision in coverage for {row['requirement']}.")
    if not allow_blocking and any(q["blocking"] for q in value["open_questions"]):
        raise SoftwareDesignError("Software design has unresolved blocking questions.")


def _validate_verdict(verdict: dict) -> None:
    try:
        jsonschema.validate(verdict, _schema("software_design_verdict.json"))
    except jsonschema.ValidationError as exc:
        raise SoftwareDesignError(f"Invalid design judgment: {exc.message}") from exc
    if verdict["decision"] != "accept" or not all(verdict["checks"].values()):
        raise SoftwareDesignError("Software design review did not accept every required check.")


def _register_validation(requirements: list[str]):
    def validate(inputs, ctx):
        try:
            validate_design(json.loads(extract_json(inputs["proposal"])), requirements)
        except (ValueError, SoftwareDesignError) as exc:
            return {"accepted": False, "validation_feedback": str(exc)}
        return {"accepted": True, "validation_feedback": ""}

    def register(registry):
        if "validate_software_design" not in registry.transforms:
            registry.register_transform(
                "validate_software_design",
                validate,
                input_schema={"proposal": str},
                output_schema={"accepted": bool, "validation_feedback": str},
            )

    return register


def _configuration(root: Path) -> tuple[OrchestraConfig, int]:
    configured = load_config(project_dir=root).role_bindings.get("software_design")
    bindings = (
        configured.bindings
        if configured
        else {
            "author": RoleBinding(model="fable"),
            "reviewer": RoleBinding(model="codex"),
            "judge_role": RoleBinding(model="fable"),
        }
    )
    roles = _resolve_compound_model_identifiers("software_design", bindings)
    if set(roles) != {"author", "reviewer", "judge_role"}:
        raise SoftwareDesignError(
            "software_design requires author, reviewer, and judge_role bindings."
        )
    reviewer = roles["reviewer"]
    for name in ("author", "judge_role"):
        if (roles[name].adapter, roles[name].model) == (reviewer.adapter, reviewer.model):
            raise SoftwareDesignError("The software-design reviewer must use a different actor.")
    if any(r.adapter not in {"claude_code_text", "codex_text"} for r in roles.values()):
        raise SoftwareDesignError(
            "Software design requires text adapters without workspace edits."
        )
    return OrchestraConfig(
        roles=roles,
        workflows={"software_design": WorkflowConfig(pattern="software_design")},
    ), configured.max_rounds if configured else 4


def _deploy(root: Path) -> None:
    for pattern in (
        "software_design.orc",
        "bounded_call.orc",
        "templates/bounded_call.md",
        "templates/software_design_*.md",
        "schemas/software_design*.json",
    ):
        for source in ASSETS.glob(pattern):
            destination = root / ".orchestra/workflows" / source.relative_to(ASSETS)
            if destination.exists():
                if destination.read_bytes() != source.read_bytes():
                    raise SoftwareDesignError(
                        f"Workflow asset differs from installed Duplo: {destination}"
                    )
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write_text(destination, source.read_text())


def _files(root: Path) -> dict[str, str]:
    from duplo.spec_reader import read_spec

    paths = {Path("SPEC.md"), Path("local.md")}
    spec = read_spec(target_dir=root)
    if spec:
        paths.update(
            Path(r.path)
            for r in spec.references
            if not r.proposed and not {"ignore", "counter-example"}.intersection(r.roles)
        )
    snapshots = {}
    for path in sorted(paths):
        full = root / path
        if full.is_file():
            snapshots[str(path)] = hashlib.sha256(full.read_bytes()).hexdigest()
        else:
            snapshots[str(path)] = "absent"
    return snapshots


def design_inputs(
    root: Path, spec_text: str, features: list[Feature], preferences: list[BuildPreferences]
) -> dict:
    snapshots = _files(root)
    reference_text = {}
    for name in snapshots:
        if name != "SPEC.md" and snapshots[name] != "absent":
            path = root / name
            if path.suffix.lower() in {".md", ".txt"} and path.stat().st_size <= 100_000:
                reference_text[name] = path.read_text(encoding="utf-8")
    return {
        "specification": spec_text,
        "requirements": sorted(
            (
                {"name": f.name, "description": f.description, "category": f.category}
                for f in features
            ),
            key=lambda f: f["name"],
        ),
        "preferences": [dataclasses.asdict(p) for p in preferences],
        "files": snapshots,
        "reference_text": reference_text,
    }


def render_design(record: dict) -> str:
    design = record["design"]
    lines = [
        "# Software design",
        "",
        f"Design revision: {record['id']}",
        f"Input digest: {record['input_digest']}",
        f"Design digest: {record['design_digest']}",
        "",
    ]
    for section in SECTIONS:
        lines.extend([f"## {section.replace('_', ' ').capitalize()}", "", design[section], ""])
    lines.extend(["## Assumptions", ""])
    lines.extend(f"- {a}" for a in design["assumptions"])
    lines.extend(["", "## Decisions", ""])
    for decision in design["decisions"]:
        lines.extend(
            [
                f"### {decision['id']}: {decision['title']}",
                "",
                decision["choice"],
                "",
                decision["rationale"],
                "",
                "Alternatives considered:",
                "",
            ]
        )
        for alternative in decision["alternatives"]:
            lines.extend([f"- {alternative['option']}: {alternative['tradeoff']}"])
        lines.extend(
            [
                "",
                f"Consequences: {decision['consequences']}",
                "",
                f"Reconsider when: {decision['reconsider_when']}",
                "",
            ]
        )
    lines.extend(["## Requirement coverage", ""])
    for row in design["coverage"]:
        lines.extend(
            [f"- {row['requirement']} ({', '.join(row['decision_ids'])}): {row['explanation']}"]
        )
    lines.extend(["", "## Open questions", ""])
    for question in design["open_questions"]:
        lines.extend([f"- {question['question']} Resolution: {question['resolution']}"])
    if not design["open_questions"]:
        lines.append("No open questions recorded by this revision.")
    lines.extend(
        [
            "",
            "## Review",
            "",
            record["review"],
            "",
            record["verdict"]["feedback"],
            "",
            f"Workflow evidence: {record['log_path']}",
            "",
        ]
    )
    return "\n".join(lines)


def _read_state(root: Path) -> dict:
    if (root / STATE_FILE).is_symlink():
        raise SoftwareDesignError("Software-design state must be a regular file.")
    state = read_json_object(root / STATE_FILE)
    if state is None:
        return {"schema_version": 1, "attempts": []}
    if (
        type(state.get("schema_version")) is not int
        or state["schema_version"] != 1
        or not isinstance(state.get("attempts"), list)
    ):
        raise SoftwareDesignError("Unsupported software-design state; preserve the file.")
    for record in state["attempts"]:
        if not isinstance(record, dict) or type(record.get("accepted")) is not bool:
            raise SoftwareDesignError("Malformed software-design attempt; preserve the file.")
        if (
            not isinstance(record.get("id"), str)
            or not record["id"]
            or not isinstance(record.get("inputs"), dict)
            or record.get("input_digest") != _digest(record["inputs"])
        ):
            raise SoftwareDesignError(
                "Malformed software-design input evidence; preserve the file."
            )
        if record["accepted"]:
            try:
                validate_design(
                    record["design"], [f["name"] for f in record["inputs"]["requirements"]]
                )
                _validate_verdict(record["verdict"])
                if record["input_digest"] != _digest(record["inputs"]) or record[
                    "design_digest"
                ] != _digest(record["design"]):
                    raise SoftwareDesignError(
                        "Software-design digest mismatch; preserve the file."
                    )
                render_design(record)
            except (KeyError, TypeError, ValueError) as exc:
                raise SoftwareDesignError(
                    "Malformed software-design evidence; preserve the file."
                ) from exc
    return dict(state)


def require_design(root: Path, inputs: dict) -> dict:
    state = _read_state(root)
    if not state["attempts"] or not state["attempts"][-1]["accepted"]:
        raise SoftwareDesignError("No accepted software design. Run `duplo design` first.")
    record = state["attempts"][-1]
    if record.get("policy_digest") != _policy_digest():
        raise SoftwareDesignError(
            "Software-design review policy changed. Run `duplo design --refresh`."
        )
    if record["input_digest"] != _digest(inputs) or inputs["files"] != _files(root):
        raise SoftwareDesignError("Software design is stale. Run `duplo design` before planning.")
    if (root / DESIGN_FILE).is_symlink():
        raise SoftwareDesignError("SOFTWARE_DESIGN.md must be a regular file.")
    old_projections = [render_design(r) for r in state["attempts"][:-1] if r["accepted"]]
    if not (root / DESIGN_FILE).exists() or (root / DESIGN_FILE).read_text() in old_projections:
        _atomic_write_text(root / DESIGN_FILE, render_design(record))
    if (root / DESIGN_FILE).read_text() != render_design(record):
        raise SoftwareDesignError(
            "SOFTWARE_DESIGN.md differs from reviewed evidence. Preserve edits in SPEC.md and restore the reviewed document."
        )
    return dict(record)


def ensure_design(root: Path, inputs: dict, *, refresh: bool = False, session=None) -> dict:
    """Reuse current evidence or run the review workflow under project ownership."""
    root = root.resolve()
    with project_owner(root):
        state = _read_state(root)
        previous = state["attempts"][-1] if state["attempts"] else None
        if (
            previous
            and previous["accepted"]
            and previous["input_digest"] == _digest(inputs)
            and not refresh
        ):
            return require_design(root, inputs)
        accepted = [r for r in state["attempts"] if r["accepted"]]
        document = root / DESIGN_FILE
        if document.is_symlink():
            raise SoftwareDesignError("SOFTWARE_DESIGN.md must be a regular file.")
        document_before = document.read_bytes() if document.exists() else None
        if document.exists() and (
            not accepted or document.read_text() != render_design(accepted[-1])
        ):
            raise SoftwareDesignError(
                "Preserve the existing SOFTWARE_DESIGN.md before generating a replacement."
            )
        if not inputs["specification"].strip() or not inputs["requirements"]:
            raise SoftwareDesignError(
                "Software design requires a specification and named requirements."
            )
        from duplo.bounded_review import ReviewSession, review_design

        config, _ = _configuration(root)
        _deploy(root)
        session = session or ReviewSession(root)
        prior = next(
            (r for r in reversed(state["attempts"]) if r.get("proposal") or r.get("design")),
            None,
        )
        record = {
            "id": uuid4().hex,
            "accepted": False,
            "policy_digest": _policy_digest(),
            "inputs": inputs,
            "input_digest": _digest(inputs),
            "log_path": str(session.path.relative_to(root)),
            "budget_id": session.current["id"],
            "review": "",
            "verdict": {},
            "error": "Review did not finish.",
            "actors": {
                name: {"adapter": role.adapter, "model": role.model}
                for name, role in config.roles.items()
            },
        }
        state["attempts"].append(record)

        def checkpoint(evidence):
            record.update(evidence)
            atomic_write_json(root / STATE_FILE, state)

        checkpoint({})
        try:
            review_design(root, inputs, config, prior, session, checkpoint)
            design = json.loads(record["proposal"])
            validate_design(design, [f["name"] for f in inputs["requirements"]])
            _validate_verdict(record["verdict"])
            if inputs["files"] != _files(root):
                raise SoftwareDesignError(
                    "Design inputs changed during review; generated evidence is stale."
                )
            if (document.read_bytes() if document.exists() else None) != document_before:
                raise SoftwareDesignError(
                    "Software design document changed during review; edits preserved."
                )
            record.update(accepted=True, design=design, design_digest=_digest(design))
            record.pop("error", None)
        except BaseException as exc:
            checkpoint({"error": str(exc) or type(exc).__name__})
            raise
        checkpoint({})
        _atomic_write_text(document, render_design(record))
        return record


def planning_context(record: dict, feature_names: list[str]) -> tuple[str, list[str]]:
    coverage = {row["requirement"]: row["decision_ids"] for row in record["design"]["coverage"]}
    unknown = set(feature_names) - coverage.keys()
    if unknown:
        raise SoftwareDesignError(f"Features absent from reviewed design: {sorted(unknown)}")
    ids = sorted({i for name in feature_names for i in coverage[name]})
    if not ids:
        ids = [d["id"] for d in record["design"]["decisions"]]
    return render_design(record), ids


def bind_phase_plan(plan, record: dict, feature_names: list[str]):
    _, ids = planning_context(record, feature_names)
    binding = f"Software design: {DESIGN_FILE}; sha256:{record['design_digest']}; decisions: {', '.join(ids)}."
    return dataclasses.replace(
        plan,
        phases=tuple(
            dataclasses.replace(phase, prose=(binding + " " + phase.prose).strip())
            for phase in plan.phases
        ),
    )


def roadmap_matches(roadmap: list[dict], record: dict) -> bool:
    return bool(roadmap) and all(
        p.get("design_digest") == record["design_digest"] for p in roadmap
    )
