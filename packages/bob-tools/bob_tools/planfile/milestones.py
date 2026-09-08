"""Structural contract for executable scaffolds and integration milestones."""

from bob_tools.planfile.iteration import _iter_tasks
from bob_tools.planfile.model import Plan, PlanValidationError, TaskStatus

MILESTONE_INSTRUCTIONS = """
Build an executable scaffold early: a small input-to-outcome path through the
assembled program, with named simulated dependencies. A blank window or
compilation alone is insufficient. Extend that path at each phase boundary and
replace simulations when the corresponding real implementation becomes available.
End every phase with a leaf [AUTO:run_cli] task carrying [milestone: scaffold] for
the first milestone, otherwise [milestone: integration], plus [demonstrates:
observable outcome], [simulated: named fakes and their planned replacement phase,
or none], [replaces: fakes replaced here, or none], and [accept: command-exit:
exact command]. Create the executable check before its milestone. Its assertions
must follow the accepted design, use the assembled program's entry path, and fail
for missing prerequisites or inconclusive evidence. Retain component tests. Do not
claim real hardware or runtime acceptance from simulated dependencies.
"""


def phase_tasks(phase):
    return list(_iter_tasks(phase.tasks)) + [
        task for section in phase.subsections for task in _iter_tasks(section.tasks)
    ]


def validate_milestones(
    plan: Plan, *, required: bool = False, continuation: bool = False
) -> None:
    phases = [(phase, phase_tasks(phase)) for phase in plan.phases]
    marked = [
        i
        for i, (_, tasks) in enumerate(phases)
        if any(key == "milestone" for t in tasks for key, _ in t.annotations)
    ]
    if not marked:
        if required:
            raise PlanValidationError(
                ["Plan requires an executable scaffold and phase milestones"]
            )
        return
    start = marked[0]
    errors = []
    if any(
        t.status is not TaskStatus.DONE for _, tasks in phases[:start] for t in tasks
    ):
        errors.append("Scaffold adoption cannot skip unfinished earlier phases")
    first = True
    for phase, tasks in phases[start:]:
        gates = [t for t in tasks if any(k == "milestone" for k, _ in t.annotations)]
        if not gates or not tasks or gates[-1] is not tasks[-1]:
            errors.append(
                f"{phase.phase_id}: end the phase with an executable milestone"
            )
        for gate in gates:
            if gate.status is TaskStatus.DONE and any(
                task.status is not TaskStatus.DONE
                for task in tasks[: tasks.index(gate)]
            ):
                errors.append(
                    f"{gate.task_id}: completed milestone cannot cover "
                    "unfinished earlier work"
                )
            fields = dict(gate.annotations)
            label = gate.task_id or phase.phase_id
            for key in ("milestone", "demonstrates", "simulated", "replaces", "accept"):
                values = [v for k, v in gate.annotations if k == key]
                if len(values) != 1 or not values[0].strip():
                    errors.append(
                        f"{label}: milestone requires one nonempty {key} annotation"
                    )
            expected = "scaffold" if first and not continuation else "integration"
            if fields.get("milestone") != expected:
                errors.append(f"{label}: expected milestone: {expected}")
            first = False
            if (
                gate.indent_level
                or gate.children
                or gate.flag_tags
                or not gate.action_tag
                or gate.action_tag[0] != "run_cli"
            ):
                errors.append(f"{label}: milestone must be a leaf AUTO:run_cli task")
            if (
                not fields.get("accept", "").startswith("command-exit: ")
                or not fields.get("accept", "")[14:].strip()
            ):
                errors.append(
                    f"{label}: milestone needs an executable command-exit acceptance"
                )
    if errors:
        raise PlanValidationError(errors)
