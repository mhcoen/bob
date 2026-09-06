"""Run software design and optional plan generation from a local specification."""

from __future__ import annotations

import argparse
from pathlib import Path

from bob_tools.json_state import atomic_write_json, read_json_object

from duplo.build_prefs import parse_build_preferences
from duplo.extractor import Feature
from duplo.file_ops import project_owner
from duplo.plan_sanity import check_plan_sanity
from duplo.planner import generate_phase_plan, save_plan
from duplo.roadmap import generate_roadmap
from duplo.software_design import SoftwareDesignError, design_inputs, ensure_design, require_design
from duplo.spec_reader import format_spec_for_prompt, read_spec, validate_for_run


def run_design(args: argparse.Namespace) -> None:
    root = Path.cwd()
    with project_owner(root):
        spec = read_spec(target_dir=root)
        if spec is None:
            raise SoftwareDesignError("Create SPEC.md with named Scope include entries first.")
        validation = validate_for_run(spec)
        if validation.errors:
            raise SoftwareDesignError("\n".join(validation.errors))
        if not spec.scope_include:
            raise SoftwareDesignError("List the requirements under Scope include in SPEC.md.")
        features = [
            Feature(name=name, description="Defined in SPEC.md.", category="requested")
            for name in spec.scope_include
        ]
        from duplo.state import read_state

        project_state = read_state(root / ".duplo/duplo.json")
        if project_state.get("features"):
            features = [
                Feature(**{k: f[k] for k in ("name", "description", "category")})
                for f in project_state["features"]
            ]
        preferences = parse_build_preferences(
            spec.architecture, structured_entries=spec.platform_entries
        )
        text = format_spec_for_prompt(spec)
        inputs = design_inputs(root, text, features, preferences)
        record = ensure_design(root, inputs, refresh=args.refresh)
        print(f"Reviewed design: SOFTWARE_DESIGN.md ({record['id']})", flush=True)
        if not args.plan:
            return
        roadmap_path = root / ".duplo/design-roadmap.json"
        saved = read_json_object(roadmap_path)
        plan_path = root / "PLAN.md"
        if saved is None:
            if plan_path.exists():
                raise SoftwareDesignError(
                    "PLAN.md already exists without a design-roadmap receipt. Preserve it before creating a new plan."
                )
            roadmap = generate_roadmap(
                "",
                features,
                preferences[0],
                spec_text=text,
                scope_include=spec.scope_include,
                software_design=record,
            )
            if not roadmap:
                raise SoftwareDesignError("Roadmap generation produced no phases.")
            require_design(root, inputs)
            saved = {
                "schema_version": 1,
                "design_digest": record["design_digest"],
                "roadmap": roadmap,
            }
            atomic_write_json(roadmap_path, saved)
        if (
            saved.get("schema_version") != 1
            or saved.get("design_digest") != record["design_digest"]
        ):
            raise SoftwareDesignError(
                "Roadmap belongs to a different design. Preserve the existing plan and roadmap before regenerating."
            )
        roadmap = saved.get("roadmap")
        if not isinstance(roadmap, list) or not roadmap:
            raise SoftwareDesignError("Malformed design roadmap; preserve the receipt.")
        from bob_tools.planfile import load
        from duplo.init import _deploy_orchestra_assets

        _deploy_orchestra_assets(root)
        if not plan_path.exists():
            save_plan(
                "# Implementation plan\n\nDerived from SOFTWARE_DESIGN.md.\n", target_dir=root
            )
        existing = load(plan_path)
        if len(existing.phases) > len(roadmap) or any(
            f"sha256:{record['design_digest']}" not in phase.prose for phase in existing.phases
        ):
            raise SoftwareDesignError("Existing plan does not match the reviewed design.")
        for phase in roadmap[len(existing.phases) :]:
            require_design(root, inputs)
            plan_before = plan_path.read_bytes()
            print(f"Planning: {phase['title']}", flush=True)
            plan = generate_phase_plan(
                "",
                features,
                preferences[0],
                phase=phase,
                spec_text=text,
                project_name="Local application",
                target_dir=root,
                software_design=record,
            )
            require_design(root, inputs)
            if plan_path.read_bytes() != plan_before:
                raise SoftwareDesignError(
                    "PLAN.md changed during phase authoring; edits preserved."
                )
            save_plan(plan, target_dir=root)
        report = check_plan_sanity(plan_path.read_text(), spec=spec)
        if not report.ok:
            raise SoftwareDesignError(f"Plan requires correction: {report.violations}")
        print(f"Plan ready: {len(roadmap)} phases in PLAN.md.", flush=True)
