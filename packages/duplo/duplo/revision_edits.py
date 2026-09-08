"""Apply model-proposed leaf-task edits through the typed plan model."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256

from bob_tools.planfile import make_task, render_plan
from bob_tools.planfile.milestones import phase_tasks
from bob_tools.planfile.model import TaskStatus
from mcloop.plan_revision import validate_revision

from duplo.software_design import SoftwareDesignError

EDIT_INSTRUCTIONS = """
Return only JSON: {"base_sha256": supplied hash, "rationale": explanation,
"operations": [...]}. Do not return a complete Markdown plan.
Allowed operations:
{"op":"insert_before" or "insert_after", "anchor":"T-000001", "key":"new1",
 "task":{"text":"description", "kind":"task" or "auto" or "user",
         "annotations":{"accept":"command-exit: exact command"}, "deps":[]}}
{"op":"update", "id":"T-000002", "task":{"text":"revised description",
 "annotations":{"accept":"command-exit: exact command"}}}
{"op":"move_before" or "move_after", "id":"T-000002", "anchor":"T-000003"}
Runtime assigns new stable IDs. Later operations may refer to earlier insertion
keys such as new1. Operations apply in order to the ORIGINAL plan, including on
correction: return a complete replacement operation list. Existing task IDs,
completion states, phase membership and accepted design cannot change. Only
pending leaf tasks can be edited or moved. Preserve all existing tasks and
required capabilities. Updates merge annotations; omit annotations to preserve
them. Classification of existing human or batch tasks cannot change. Use auto
for executable milestones. Supply milestone/demonstrates/simulated/replaces and
accept annotations. New tasks are always pending. Keep the scaffold small and
reuse existing completed implementations; status DONE means already implemented,
not a future prerequisite. Do not manufacture implementation or acceptance evidence.
Adopt milestones in the first phase with pending work. Do not add work to earlier
completed phases. The first new milestone is scaffold even when its phase ID is
not phase_001; all later milestones are integration. Insert a small executable
path before further subsystem work, then extend it. Return at most 30000 characters.
Maximum 128 operations. Prefer extending existing pending integration tasks over
adding duplicate suites. Create any needed milestone command before invoking it.
"""


def plan_hash(plan):
    return sha256(render_plan(plan).encode()).hexdigest()


def _locate(plan, identity):
    for pi, phase in enumerate(plan.phases):
        containers = [(None, phase.tasks)] + [
            (si, section.tasks) for si, section in enumerate(phase.subsections)
        ]
        for si, tasks in containers:
            for index, task in enumerate(tasks):
                if task.task_id == identity:
                    return pi, si, index, task, list(tasks)
    raise SoftwareDesignError(f"Task {identity} must identify a phase or subsection root task")


def _put(plan, pi, si, tasks):
    phase = plan.phases[pi]
    if si is None:
        phase = replace(phase, tasks=tuple(tasks))
    else:
        sections = list(phase.subsections)
        sections[si] = replace(sections[si], tasks=tuple(tasks))
        phase = replace(phase, subsections=tuple(sections))
    phases = list(plan.phases)
    phases[pi] = phase
    return replace(plan, phases=tuple(phases))


def _task(data, identity, aliases, previous=None):
    if not isinstance(data, dict) or set(data) - {"text", "kind", "annotations", "deps"}:
        raise SoftwareDesignError("Task edits require text, optional kind, annotations and deps")
    if not isinstance(data.get("text"), str) or not data["text"].strip():
        raise SoftwareDesignError("Task description must be nonempty")
    old_kind = (
        "auto"
        if previous and previous.action_tag
        else ("user" if previous and "USER" in previous.flag_tags else "task")
    )
    kind = data.get("kind", old_kind)
    if kind not in {"task", "auto", "user"} or (previous and kind != old_kind):
        raise SoftwareDesignError("Invalid task kind or change to existing task classification")
    fields = data.get("annotations", {})
    if not isinstance(fields, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in fields.items()
    ):
        raise SoftwareDesignError("Annotations must map names to strings")
    annotations = dict(previous.annotations) if previous else {}
    annotations.update(fields)
    deps = data.get("deps", list(previous.deps) if previous else [])
    if not isinstance(deps, list) or any(not isinstance(d, str) for d in deps):
        raise SoftwareDesignError("Dependencies must be task IDs or earlier insertion keys")
    return make_task(
        "" if kind == "auto" else data["text"],
        task_id=identity,
        flag_tags=previous.flag_tags if previous else (("USER",) if kind == "user" else ()),
        action_tag=((previous.action_tag[0] if previous else "run_cli"), data["text"])
        if kind == "auto"
        else None,
        annotations=tuple(annotations.items()),
        deps=tuple(aliases.get(d, d) for d in deps),
        ruled_out=previous.ruled_out if previous else (),
        created_at=previous.created_at if previous else None,
    )


def apply_edits(before, response):
    if (
        not isinstance(response, dict)
        or set(response) != {"base_sha256", "rationale", "operations"}
        or response["base_sha256"] != plan_hash(before)
    ):
        raise SoftwareDesignError("Revision must identify the original plan hash")
    if not isinstance(response["rationale"], str) or not response["rationale"].strip():
        raise SoftwareDesignError("Revision needs a rationale")
    operations = response["operations"]
    if not isinstance(operations, list) or not 1 <= len(operations) <= 128:
        raise SoftwareDesignError("Revision requires 1 to 128 operations")
    plan = before
    first_pending = next(
        (
            i
            for i, phase in enumerate(before.phases)
            if any(t.status is TaskStatus.TODO for t in phase_tasks(phase))
        ),
        None,
    )
    if first_pending is None:
        raise SoftwareDesignError("No pending implementation remains to revise")
    identities = {t.task_id for p in before.phases for t in phase_tasks(p)}
    serial = max(int(identity[2:]) for identity in identities)
    aliases = {}
    for op in operations:
        if not isinstance(op, dict):
            raise SoftwareDesignError("Malformed revision operation")
        kind = op.get("op")
        if kind in {"insert_before", "insert_after"}:
            if set(op) != {"op", "anchor", "key", "task"}:
                raise SoftwareDesignError("Insertion requires anchor, key and task")
            key = op["key"]
            if not isinstance(key, str) or not key or key in aliases or key in identities:
                raise SoftwareDesignError("Insertion keys must be new and unique")
            pi, si, index, _, tasks = _locate(plan, aliases.get(op["anchor"], op["anchor"]))
            if pi < first_pending:
                raise SoftwareDesignError("Keep completed earlier phases unchanged")
            serial += 1
            identity = f"T-{serial:06d}"
            if identity in aliases:
                raise SoftwareDesignError("Insertion keys cannot shadow assigned task IDs")
            new = _task(op["task"], identity, aliases)
            aliases[key] = identity
            identities.add(identity)
            tasks.insert(index + (kind == "insert_after"), new)
            plan = _put(plan, pi, si, tasks)
        elif kind in {"update", "move_before", "move_after"}:
            expected = {"op", "id", "task" if kind == "update" else "anchor"}
            if set(op) != expected:
                raise SoftwareDesignError("Malformed update or move operation")
            pi, si, index, previous, tasks = _locate(plan, aliases.get(op["id"], op["id"]))
            if previous.status is not TaskStatus.TODO or previous.children:
                raise SoftwareDesignError("Only pending leaf tasks can be edited or moved")
            if kind == "update":
                tasks[index] = _task(op["task"], previous.task_id, aliases, previous)
                plan = _put(plan, pi, si, tasks)
            else:
                tasks.pop(index)
                plan = _put(plan, pi, si, tasks)
                ap, section, index, _, destination = _locate(
                    plan, aliases.get(op["anchor"], op["anchor"])
                )
                if (ap, section) != (pi, si):
                    raise SoftwareDesignError("Move tasks only within their existing container")
                destination.insert(index + (kind == "move_after"), previous)
                plan = _put(plan, ap, section, destination)
        else:
            raise SoftwareDesignError(f"Unknown revision operation: {kind}")
    validate_revision(before, plan)
    return plan
