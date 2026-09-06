"""Run software design and optional plan generation from a local specification."""

from __future__ import annotations

import argparse
from pathlib import Path

from duplo.build_prefs import parse_build_preferences
from duplo.extractor import Feature
from duplo.file_ops import project_owner
from duplo.software_design import SoftwareDesignError, design_inputs, ensure_design
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
        from duplo.bounded_review import ReviewSession

        session = ReviewSession(root, reset=getattr(args, "new_review_budget", False))
        record = ensure_design(root, inputs, refresh=args.refresh, session=session)
        print(f"Reviewed design: SOFTWARE_DESIGN.md ({record['id']})", flush=True)
        if not args.plan:
            return
        from duplo.design_plan import generate_design_plan

        generate_design_plan(root, inputs, record, spec, session)
