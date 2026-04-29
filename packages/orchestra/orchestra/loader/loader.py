"""Loader entry point: parse + validate a workflow file."""

from __future__ import annotations

from pathlib import Path

from orchestra.loader.parser import parse_workflow
from orchestra.loader.validator import validate
from orchestra.registry import ProfileRegistry
from orchestra.spine import Workflow


def load_workflow(path: str | Path, registry: ProfileRegistry) -> Workflow:
    """Load and validate a workflow file."""
    src_path = Path(path).resolve()
    with open(src_path, encoding="utf-8") as fh:
        source = fh.read()
    workflow = parse_workflow(source, src_path)
    validate(workflow, registry)
    return workflow
