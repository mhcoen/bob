"""Council-mediated plan-authoring path.

Optional alternative to the single-actor ``query()`` path in
``duplo.planner.generate_phase_plan``. When enabled, plan generation
fans out to four proposer actors via Orchestra's ``council_four``
workflow and returns the synthesizer's plan body.

Activation:

  - CLI flag ``--use-council`` on top-level ``duplo`` (sets the env
    var below).
  - Env var ``DUPLO_USE_COUNCIL=1``.
  - ``--no-council`` (or ``DUPLO_NO_COUNCIL=1``) overrides; useful
    in CI to force the legacy path even when env defaults are
    council-on.

Default-on auto-detection (when a project has the five council role
bindings configured) is intentionally deferred to a follow-up commit
after real-API end-to-end validation.

Hardcoded fallback role bindings, used when the project has no
``.orchestra/config.json`` (or one without the council roles):

  framer:             claude_code_text          (haiku)
  proposer_code:      claude_code_text          (fable)
  proposer_codex:     codex_text                (gpt-5.5)
  proposer_kimi:      claude_code_text_kimi     (kimi-k2.6)
  proposer_deepseek:  claude_code_text_deepseek (deepseek-v4-pro)
  synthesizer:        claude_code_text          (fable)

The four proposers resolve to pairwise distinct (adapter, model)
tuples (the council fan-out value). The synthesizer shares a model
string with proposer_code; this is permitted by Orchestra's
``_validate_council_four`` -- distinct ROLE BINDINGS, not distinct
MODEL STRINGS, are the rule. See
``design/council-actor-bindings.md`` in the Orchestra tree for the
rationale.

Audit layout. After a successful run, Duplo writes:

  <project>/.duplo/audits/council/<run_id>/
    brief.md            council_brief artifact
    proposal_code.md
    proposal_codex.md
    proposal_kimi.md
    proposal_deepseek.md
    plan.md             synthesizer's plan body (also returned to
                        planner.generate_phase_plan and fed to
                        save_plan as before)
    verdict.json        judge_verdict json
    run_meta.json       run_id, terminal, durations summary,
                        question, log path

PLAN.md is still written by ``saver.save_plan`` from the returned
plan text. Downstream consumers see no change in PLAN.md location.

Cost/latency note. Council = 1 framer + 4 parallel proposers + 1
synthesizer = ~6 LLM calls. Wall-clock is roughly 3-4x the legacy
single-actor path. A one-line stderr notice fires on every council
invocation; document the profile separately in Duplo's README.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from bob_tools.planfile import (
    Phase,
    Plan,
    PlanSyntaxError,
    PlanValidationError,
    RuledOut,
    Subsection,
    Task,
    assert_mcloop_canonical,
    make_task,
    migrate,
    parse_plan,
    validate_plan,
)

from duplo import call_log
from duplo.acceptance import ensure_acceptance_annotations
from duplo.batch_coverage import ensure_batch_test_coverage
from duplo.canonical_consistency import (
    validate_spec_pyproject_runsh_consistency,
)

_ENABLE_ENV = "DUPLO_USE_COUNCIL"
_DISABLE_ENV = "DUPLO_NO_COUNCIL"
_CONFIG_PATH_ENV = "DUPLO_COUNCIL_CONFIG"

_FALLBACK_ROLE_BINDINGS: dict[str, dict[str, Any]] = {
    "framer": {"adapter": "claude_code_text", "model": "haiku"},
    "proposer_code": {"adapter": "claude_code_text", "model": "fable"},
    "proposer_codex": {"adapter": "codex_text", "model": "gpt-5.5"},
    "proposer_kimi": {
        "adapter": "claude_code_text_kimi",
        "model": "kimi-k2.6",
    },
    "proposer_deepseek": {
        "adapter": "claude_code_text_deepseek",
        "model": "deepseek-v4-pro",
    },
    "synthesizer": {"adapter": "claude_code_text", "model": "fable"},
}

_COUNCIL_REQUIRED_ROLES: tuple[str, ...] = (
    "framer",
    "proposer_code",
    "proposer_codex",
    "proposer_kimi",
    "proposer_deepseek",
    "synthesizer",
)


def make_duplo_progress_callback() -> Callable[[Any], None]:
    """Return a ``ProgressCallback`` that prints duplo-shaped lines.

    One line per ``state_enter`` and ``state_exit`` from
    orchestra.run_workflow:

      [duplo] state=propose_kimi model=kimi-k2.6 status=running
      [duplo] state=propose_kimi model=kimi-k2.6 elapsed=168.9s status=complete

    Fan-out boundaries (``fan_out_start`` / ``fan_out_end``) print a
    single header line so the user sees the parallel block exists;
    individual child enter/exit events still print as ordinary
    state transitions inside the block.

    ``actor_progress`` events surface a two-line ticker matching
    orchestra's ``stderr_reporter`` shape: a duplo-style "still running"
    line and an indented ``    running: <activity>`` line pulled from
    the subprocess adapter's live-activity getter. This is the duplo
    half of the T-000001 (orchestra-side commit c38a5005) live-activity
    surfacing pattern. Sessions with no live activity emit just the
    "still running" line; no spurious empty activity lines.

    Lines go to stderr so stdout-only consumers (smoke harnesses,
    pipelines) are unaffected. The callback is thread-safe: orchestra
    fan-out workers may emit ``state_exit`` events from worker
    threads, and a module-level lock keeps line writes from
    interleaving.
    """
    import shutil
    import threading

    from orchestra.adapters import _subprocess as _subprocess_mod

    _ACTIVITY_LINE_PREFIX = "    running: "

    lock = threading.Lock()

    def _emit(line: str) -> None:
        with lock:
            sys.stderr.write(line + "\n")
            sys.stderr.flush()

    def _emit_activity_line() -> None:
        try:
            activity = _subprocess_mod.get_current_activity()
        except Exception:
            return
        if not activity:
            return
        try:
            columns = shutil.get_terminal_size((80, 24)).columns
        except (OSError, ValueError):
            columns = 80
        line = _ACTIVITY_LINE_PREFIX + activity
        if columns > 1 and len(line) > columns:
            line = line[: max(columns - 1, len(_ACTIVITY_LINE_PREFIX))] + "…"
        _emit(line)

    def callback(event: Any) -> None:
        kind = event.kind
        if kind == "fan_out_start":
            children = event.children or ()
            count = len(children)
            _emit(f"[duplo] fan_out start ({count} parallel proposers)")
            return
        if kind == "fan_out_end":
            _emit("[duplo] fan_out end")
            return
        if kind == "actor_progress":
            model = event.model or event.adapter or "transform"
            elapsed = event.elapsed_seconds or 0.0
            _emit(
                f"[duplo] state={event.state_name} model={model} "
                f"elapsed={elapsed:.1f}s status=running"
            )
            _emit_activity_line()
            return
        if kind not in ("state_enter", "state_exit"):
            return
        model = event.model or event.adapter or "transform"
        if kind == "state_enter":
            _emit(f"[duplo] state={event.state_name} model={model} status=running")
            return
        elapsed = event.elapsed_seconds
        if elapsed is None:
            _emit(f"[duplo] state={event.state_name} model={model} status=complete")
        else:
            _emit(
                f"[duplo] state={event.state_name} model={model} "
                f"elapsed={elapsed:.1f}s status=complete"
            )

    return callback


_TRUTHY = ("1", "true", "yes", "on")


class CouncilError(RuntimeError):
    """Raised when the council path cannot run or returns no plan."""


# Canonical-mode plan body parsing. The header regex is shared with
# ``compute_required_phase_id`` to detect ``## Phase <id>:`` lines in
# an existing PLAN.md on disk. Per-synthesis content validation has
# moved into bob_tools.planfile (parse_plan + validate_plan
# constructed=True + assert_mcloop_canonical); this module only needs
# the existing-PLAN.md header scanner.
_CANONICAL_PHASE_HEADER_RE = re.compile(
    r"^##\s+Phase\s+(?P<id>[A-Za-z0-9_]+):\s+(?P<title>.+?)\s*$"
)
_STRICT_PHASE_ID_RE = re.compile(r"^phase_\d{3,}$")


def _phase_id_numeric_suffix(phase_id: str) -> int | None:
    """Return the integer suffix of a strict ``phase_NNN`` id, or None
    when the id is not in the strict form.

    The validator's monotonic-order check (Check 6) compares phase ids
    by their numeric suffix; strings like ``phase_001`` and
    ``phase_002`` are ordered numerically, not lexicographically.
    """
    match = _STRICT_PHASE_ID_RE.match(phase_id)
    if match is None:
        return None
    return int(phase_id[len("phase_") :])


def compute_required_phase_id(plan_path: Path) -> str:
    """Compute the next phase_id Duplo will demand for canonical
    synthesis against ``plan_path``.

    Reads the existing PLAN.md (if present), extracts every
    ``## Phase phase_NNN:`` header, and returns
    ``f"phase_{(highest_NNN + 1):03d}"``. When PLAN.md is absent or
    has no recognized phase headers, returns ``"phase_001"``.

    Codex's safe rule (per the directive that landed alongside this
    helper): use ``highest + 1``, NOT the smallest gap. A PLAN.md
    containing ``phase_001`` and ``phase_003`` returns
    ``"phase_004"``, not ``"phase_002"``. This avoids accidentally
    re-using an id that an earlier failed run wrote and then rolled
    back; gap-filling would let stale lineage state coexist with
    new entries under the same identifier.
    """
    if not plan_path.is_file():
        return "phase_001"
    text = plan_path.read_text(encoding="utf-8")
    highest = 0
    for line in text.splitlines():
        match = _CANONICAL_PHASE_HEADER_RE.match(line)
        if match is None:
            continue
        suffix = _phase_id_numeric_suffix(match.group("id"))
        if suffix is None:
            continue
        if suffix > highest:
            highest = suffix
    return f"phase_{highest + 1:03d}"


def is_enabled() -> bool:
    """Return True when council mode should run for this invocation.

    Explicit disable wins. Otherwise return whatever the enable env
    var says. Auto-detect-from-config is not implemented here; that
    lands as a follow-up after real-API validation.
    """
    if _env_truthy(_DISABLE_ENV):
        return False
    return _env_truthy(_ENABLE_ENV)


def set_enabled(value: bool) -> None:
    """Set the enable env var. Called by ``duplo.main`` from CLI flags."""
    if value:
        os.environ[_ENABLE_ENV] = "1"
        os.environ.pop(_DISABLE_ENV, None)
    else:
        os.environ[_DISABLE_ENV] = "1"
        os.environ.pop(_ENABLE_ENV, None)


def set_config_path(path: str | None) -> None:
    """Set the explicit council-config path. Called by ``duplo.main``."""
    if path is None:
        os.environ.pop(_CONFIG_PATH_ENV, None)
    else:
        os.environ[_CONFIG_PATH_ENV] = path


def _rebuild_task_constructed(task: Task) -> Task:
    """Rebuild ``task`` via :func:`make_task` so it is field-stable.

    The synthesizer's plan body comes through :func:`parse_plan`, which
    attaches source positions and may absorb stray prose into
    ``trailing_lines``. The constructed-mode validator in
    :func:`validate_plan` rejects nonempty ``trailing_lines`` and runs
    a per-task field-stability harness. Rebuilding each task with
    :func:`make_task` and the canonical structural fields (text, status,
    tags, annotations, deps, ruled_out, children) drops position
    metadata and forces ``trailing_lines=()`` so the result is a
    constructed plan, not a parsed one.

    Children are rebuilt recursively. Task ids on the parsed tasks are
    ``None`` because the canonical synthesizer template does not author
    them; :func:`migrate` assigns ids on the rebuilt plan later.
    """
    rebuilt_children = tuple(_rebuild_task_constructed(child) for child in task.children)
    rebuilt_ruled_out = tuple(RuledOut(text=ruled.text, line_number=0) for ruled in task.ruled_out)
    return make_task(
        task.text,
        status=task.status,
        flag_tags=task.flag_tags,
        action_tag=task.action_tag,
        annotations=task.annotations,
        deps=task.deps,
        children=rebuilt_children,
        ruled_out=rebuilt_ruled_out,
        task_id=task.task_id,
    )


def _rebuild_phase_constructed(phase: Phase, *, ordinal: int) -> Phase:
    """Rebuild ``phase`` with constructed-mode tasks and stable ordinal."""
    rebuilt_tasks = tuple(_rebuild_task_constructed(t) for t in phase.tasks)
    rebuilt_subsections = tuple(
        Subsection(
            title=sub.title,
            prose=sub.prose,
            tasks=tuple(_rebuild_task_constructed(t) for t in sub.tasks),
            line_number=0,
        )
        for sub in phase.subsections
    )
    phase_id_source = phase.phase_id_source
    # Normalize `explicit_header` to `explicit_comment` so the renderer
    # emits a `<!-- phase_id: ... -->` line that survives round-trip
    # under constructed-mode field-stability checks.
    if phase_id_source == "explicit_header":
        phase_id_source = "explicit_comment"
    return Phase(
        phase_id=phase.phase_id,
        phase_id_source=phase_id_source,
        ordinal=ordinal,
        keyword=phase.keyword,
        title=phase.title,
        prose=phase.prose,
        subsections=rebuilt_subsections,
        tasks=rebuilt_tasks,
        line_number=0,
    )


def typed_plan_from_synthesizer_text(
    plan_text: str,
    *,
    required_phase_id: str,
) -> Plan:
    """Convert the synthesizer's plan body to a validated typed Plan.

    The synthesizer emits a markdown plan body following the canonical
    Slice C contract (``## Phase phase_NNN: title`` headers + per-task
    ``- [ ]`` checklist lines). This helper:

    1. Parses the body via :func:`bob_tools.planfile.parse_plan` so the
       checklist structure becomes typed Phase / Task values.
    2. Rebuilds each task via :func:`make_task` to drop parser-side
       source positions and ``trailing_lines`` so the result is a
       constructed plan (the v4 Contract 4 invariant).
    3. Sets ``magic_version=1`` (the construction-mode requirement) and
       assigns task ids via :func:`migrate`.
    4. Validates with :func:`validate_plan` ``constructed=True``.
    5. Validates with :func:`assert_mcloop_canonical` so mcloop's
       canonical-input contract holds.
    6. Asserts ``required_phase_id`` is present in the resulting plan;
       the runtime owns phase identity and the synthesizer must honor
       the supplied value.

    Returns the validated :class:`Plan`. Any synthesis-time failure
    surfaces as :class:`bob_tools.planfile.PlanSyntaxError` (malformed
    body) or :class:`PlanValidationError` (constructed-mode or
    canonical-contract violation); both propagate to the caller.
    """
    try:
        parsed = parse_plan(plan_text)
    except PlanSyntaxError as exc:
        raise PlanValidationError(
            [f"synthesizer plan body could not be parsed as PLAN.md: {exc}"]
        ) from exc

    # A canonical phase body carries phases only. ``## Bugs`` is an
    # mcloop convention appended by ``duplo fix``, never something the
    # synthesizer may emit; silently dropping it (the constructed plan
    # sets ``bugs=None``) would lose whatever the model put there without
    # telling the loop to re-draft. Reject it so the validation gate can
    # feed the synthesizer named feedback instead.
    if parsed.bugs is not None:
        raise PlanValidationError(
            [
                "synthesizer plan body contains a '## Bugs' section; a "
                "canonical phase plan must contain phases only. Remove the "
                "'## Bugs' heading and fold any real work into a phase task."
            ]
        )

    rebuilt_phases = tuple(
        _rebuild_phase_constructed(phase, ordinal=index + 1)
        for index, phase in enumerate(parsed.phases)
    )
    constructed = Plan(
        magic_version=1,
        project_title=parsed.project_title,
        preamble=parsed.preamble,
        phases=rebuilt_phases,
        bugs=None,
        source_path=None,
    )
    # Co-locate each module-creating batch's covering test as a sibling
    # task within the same batch, before ids are assigned, so the batch is
    # self-contained for mcloop's coverage gate (created code and its
    # exercising test are accepted together).
    constructed = ensure_batch_test_coverage(constructed)
    migrated = migrate(constructed)
    validate_plan(migrated)
    migrated = ensure_acceptance_annotations(migrated)
    validate_plan(migrated, constructed=True)
    assert_mcloop_canonical(migrated)

    actual_phase_ids = [phase.phase_id for phase in migrated.phases]
    if required_phase_id not in actual_phase_ids:
        raise PlanValidationError(
            [
                f"required_phase_id {required_phase_id!r} not present in "
                f"synthesized plan body; synthesizer emitted "
                f"{actual_phase_ids!r} instead. The runtime computes the "
                "phase_id deterministically from the existing PLAN.md; "
                "the synthesizer must use it verbatim."
            ]
        )

    return migrated


# Backward-compat private alias. Kept until callers migrate; do not
# remove without a grep across consumers.
_typed_plan_from_synthesizer_text = typed_plan_from_synthesizer_text


def author_phase_plan(
    *,
    prompt: str,
    system: str,
    phase_num: int | str,
    project_dir: Path | None = None,
) -> Plan:
    """Run ``council_four`` and return the synthesizer's plan as a typed Plan.

    ``prompt`` and ``system`` are the same strings the legacy
    ``query()`` path would send: prompt is the rendered reference
    material body, system is the planner's _PHASE_SYSTEM directive.
    They are concatenated into the council's ``state`` external
    input. ``question`` is fixed by template at this layer
    (canonical Duplo phase authoring); when Plan Ledger lands and
    re-authoring becomes a real path, the question shape will be
    revisited.

    The synthesizer's plan-body artifact is converted to a typed
    :class:`bob_tools.planfile.Plan` via
    :func:`typed_plan_from_synthesizer_text`: the body is parsed,
    rebuilt as a constructed plan, ids are assigned, and the result is
    validated under both ``validate_plan(constructed=True)`` and
    :func:`assert_mcloop_canonical`. Callers receive structured data and
    must persist via :func:`bob_tools.planfile.save`, not raw markdown
    ``write_text``.

    Raises ``CouncilError`` on terminal != "done", missing plan
    artifact, or import-time failures.
    :class:`bob_tools.planfile.PlanValidationError` surfaces when the
    synthesized body fails construction or canonical validation; the
    audit dir is still written so the offending body is recoverable.
    """
    project_dir = (project_dir or Path.cwd()).resolve()

    # Pre-flight: SPEC.md, pyproject.toml, and run.sh must agree on
    # the project's script and package identifiers. Drift between
    # these declarations otherwise gets papered over by the
    # synthesizer at plan-authoring time, producing tasks that
    # invoke a script under a different name than SPEC promises or
    # that import a package the pyproject does not declare. Same
    # fail-closed shape as Slice C lineage validation; the council
    # is not invoked when consistency fails.
    validate_spec_pyproject_runsh_consistency(project_dir)

    _print_council_notice()

    try:
        from orchestra import run_workflow
        from orchestra.config import (
            ConfigError,
            OrchestraConfig,
            RoleBinding,
            WorkflowConfig,
            load_config,
        )
    except ImportError as exc:
        raise CouncilError(
            "council mode requires the 'orchestra' package; "
            "install it (pip install -e /path/to/orchestra) or run "
            "without --use-council for the legacy single-actor path"
        ) from exc

    cfg = _load_or_fallback_config(
        project_dir,
        load_config=load_config,
        config_cls=OrchestraConfig,
        role_cls=RoleBinding,
        workflow_cls=WorkflowConfig,
        config_error=ConfigError,
    )

    state_text = _build_state_text(prompt=prompt, system=system)
    question = f"Author the Phase {phase_num} plan from the reference material above."
    # Compute required_phase_id deterministically from the existing
    # PLAN.md (Codex's safe rule: highest+1, NOT the smallest gap).
    # The synthesizer is told to use this id verbatim; the canonical
    # validator re-checks post-synthesis. See the
    # "Per-call model authority is the wrong ownership boundary"
    # section in orchestra/design/synthesizer-output-contract.md.
    plan_path = project_dir / "PLAN.md"
    required_phase_id = compute_required_phase_id(plan_path)
    inputs: dict[str, Any] = {
        "state": state_text,
        "question": question,
        "ledger_slice": "",
        "design_context": "",
        "required_phase_id": required_phase_id,
    }

    audits_root = project_dir / ".duplo" / "audits" / "council"
    audits_root.mkdir(parents=True, exist_ok=True)
    started_at = time.time()
    try:
        result = run_workflow(
            "council_four_canonical",
            inputs,
            cfg,
            project_dir=project_dir,
            data_root=audits_root / "_runs",
            progress_callback=make_duplo_progress_callback(),
        )
    except Exception as exc:  # noqa: BLE001 — surface any wiring failure
        raise CouncilError(f"council_four_canonical invocation failed: {exc}") from exc
    elapsed_s = time.time() - started_at

    if result.terminal != "done":
        verdict = result.artifacts.get("judge_verdict")
        decision = (
            verdict.value.get("decision")
            if verdict is not None and isinstance(verdict.value, dict)
            else None
        )
        feedback = (
            verdict.value.get("feedback")
            if verdict is not None and isinstance(verdict.value, dict)
            else None
        )
        raise CouncilError(
            f"council_four_canonical did not accept (terminal={result.terminal!r}, "
            f"decision={decision!r}). Feedback: {feedback!r}. "
            f"Run audit at {audits_root / result.run_id}"
        )

    plan_view = result.artifacts.get("plan")
    if plan_view is None or not isinstance(plan_view.value, str):
        raise CouncilError("council_four_canonical accepted but produced no 'plan' artifact")
    plan_text: str = plan_view.value.strip()
    if not plan_text:
        raise CouncilError("council_four_canonical accepted but the 'plan' artifact is empty")

    _write_audit(
        audits_root / result.run_id,
        result=result,
        question=question,
        elapsed_s=elapsed_s,
    )

    # Index this council-authored phase in the duplo run directory so a
    # single run dir is the complete record of every LLM call regardless
    # of path. The orchestra council route captures each per-actor call
    # inside its own run dir; here we write a pointer to it keyed by the
    # same ``call_site`` the legacy ``query()`` route uses for a phase.
    call_log.log_council_phase(
        call_site=f"phase_plan:{required_phase_id}",
        orchestra_run_id=result.run_id,
        transcript_path=result.log_path,
        extra={"audit_dir": str(audits_root / result.run_id)},
    )

    # Canonical validation has moved into bob_tools.planfile. The plan
    # body is parsed, rebuilt as a constructed plan, ids are assigned,
    # and the result is validated under both
    # ``validate_plan(constructed=True)`` and ``assert_mcloop_canonical``
    # so the data the caller persists is structured and McLoop-executable.
    # Audit dir is already written; PLAN.md is NOT written by
    # ``author_phase_plan`` itself, so a raise here means PLAN.md stays
    # untouched.
    return typed_plan_from_synthesizer_text(plan_text, required_phase_id=required_phase_id)


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def _print_council_notice() -> None:
    print(
        "[duplo] council mode: ~6 LLM calls (framer + 4 proposers + "
        "synthesizer); estimated 3-4x wall-clock vs legacy "
        "single-actor mode.",
        file=sys.stderr,
    )


def _build_state_text(*, prompt: str, system: str) -> str:
    parts = []
    if system.strip():
        parts.append("System directive for plan authoring:")
        parts.append(system.strip())
        parts.append("")
    parts.append("Reference material:")
    parts.append(prompt.strip())
    return "\n".join(parts)


def _load_or_fallback_config(
    project_dir: Path,
    *,
    load_config: Any,
    config_cls: Any,
    role_cls: Any,
    workflow_cls: Any,
    config_error: type[Exception],
) -> Any:
    """Resolve the OrchestraConfig used for the council run.

    Order of preference:

    1. Explicit ``DUPLO_COUNCIL_CONFIG`` path (an .orchestra/config.json
       file). Loaded via its project dir (two levels up, since
       ``load_config`` re-appends ``.orchestra/config.json``).
    2. Project's ``.orchestra/config.json`` if present and contains
       all six required council role bindings (framer, four
       proposers, synthesizer).
    3. Hardcoded fallback bindings.

    Always ensures a ``council_four`` workflow entry exists in the
    returned config; if a project config has the roles but no
    workflow entry, one is injected.
    """
    explicit = os.environ.get(_CONFIG_PATH_ENV)
    if explicit:
        explicit_path = Path(explicit).expanduser()
        if not explicit_path.exists():
            raise CouncilError(f"DUPLO_COUNCIL_CONFIG path does not exist: {explicit_path}")
        try:
            # .parent is the .orchestra dir; the project root load_config
            # expects is two levels up (see docstring).
            cfg = load_config(project_dir=explicit_path.parent.parent)
        except config_error as exc:
            raise CouncilError(f"failed to load council config at {explicit_path}: {exc}") from exc
        return _ensure_council_workflow(cfg, config_cls=config_cls, workflow_cls=workflow_cls)

    try:
        project_cfg = load_config(project_dir=project_dir)
    except config_error as exc:
        raise CouncilError(
            f"failed to load .orchestra/config.json at {project_dir}: {exc}"
        ) from exc

    missing = [r for r in _COUNCIL_REQUIRED_ROLES if r not in project_cfg.roles]
    if not missing:
        return _ensure_council_workflow(
            project_cfg, config_cls=config_cls, workflow_cls=workflow_cls
        )

    return _build_fallback_config(
        config_cls=config_cls,
        role_cls=role_cls,
        workflow_cls=workflow_cls,
    )


_CANONICAL_WORKFLOW_NAME = "council_four_canonical"
_REAUTHOR_WORKFLOW_NAME = "council_four_reauthor"


def _ensure_council_workflow(cfg: Any, *, config_cls: Any, workflow_cls: Any) -> Any:
    """Ensure both council variants exist in ``cfg.workflows``.

    The canonical and reauthor workflows are independently named in
    orchestra after the Slice D smoke split (see commit ee44ba5);
    duplo adds either if missing. Callers that already declared the
    new names see no change. Pre-split callers that only declared
    ``council_four`` get the new entries injected; the deprecated
    ``council_four`` name is left untouched in their config so any
    other code path that references it keeps working for one
    release while the DeprecationWarning fires from orchestra.
    """
    needed = (
        _CANONICAL_WORKFLOW_NAME,
        _REAUTHOR_WORKFLOW_NAME,
    )
    if all(name in cfg.workflows for name in needed):
        return cfg
    new_workflows = dict(cfg.workflows)
    for name in needed:
        if name not in new_workflows:
            new_workflows[name] = workflow_cls(pattern=name)
    return config_cls(
        roles=dict(cfg.roles),
        workflows=new_workflows,
        verbs=dict(cfg.verbs),
        criteria=cfg.criteria,
    )


def _build_fallback_config(
    *,
    config_cls: Any,
    role_cls: Any,
    workflow_cls: Any,
) -> Any:
    roles = {
        name: role_cls(adapter=spec["adapter"], model=spec["model"])
        for name, spec in _FALLBACK_ROLE_BINDINGS.items()
    }
    workflows = {
        _CANONICAL_WORKFLOW_NAME: workflow_cls(pattern=_CANONICAL_WORKFLOW_NAME),
        _REAUTHOR_WORKFLOW_NAME: workflow_cls(pattern=_REAUTHOR_WORKFLOW_NAME),
    }
    return config_cls(roles=roles, workflows=workflows)


def _write_audit(
    audit_dir: Path,
    *,
    result: Any,
    question: str,
    elapsed_s: float,
) -> None:
    audit_dir.mkdir(parents=True, exist_ok=True)

    def _put(name: str, key: str) -> None:
        view = result.artifacts.get(key)
        if view is None or view.value is None:
            return
        body = (
            view.value
            if isinstance(view.value, str)
            else json.dumps(view.value, indent=2, sort_keys=True)
        )
        (audit_dir / name).write_text(body)

    _put("brief.md", "council_brief")
    _put("proposal_code.md", "proposal_code")
    _put("proposal_codex.md", "proposal_codex")
    _put("proposal_kimi.md", "proposal_kimi")
    _put("proposal_deepseek.md", "proposal_deepseek")
    _put("plan.md", "plan")

    verdict_view = result.artifacts.get("judge_verdict")
    if verdict_view is not None:
        verdict_path = audit_dir / "verdict.json"
        verdict_path.write_text(json.dumps(verdict_view.value, indent=2, sort_keys=True))

    run_meta = {
        "run_id": result.run_id,
        "terminal": result.terminal,
        "elapsed_s": round(elapsed_s, 2),
        "question": question,
        "log_path": str(result.log_path),
    }
    (audit_dir / "run_meta.json").write_text(json.dumps(run_meta, indent=2, sort_keys=True))
