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
  proposer_code:      claude_code_text          (opus)
  proposer_codex:     codex_text                (gpt-5.5)
  proposer_kimi:      claude_code_text_kimi     (kimi-k2.6)
  proposer_deepseek:  claude_code_text_deepseek (deepseek-v4-pro)
  synthesizer:        claude_code_text          (opus)

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

from duplo.canonical_consistency import (
    validate_spec_pyproject_runsh_consistency,
)

_ENABLE_ENV = "DUPLO_USE_COUNCIL"
_DISABLE_ENV = "DUPLO_NO_COUNCIL"
_CONFIG_PATH_ENV = "DUPLO_COUNCIL_CONFIG"

_FALLBACK_ROLE_BINDINGS: dict[str, dict[str, Any]] = {
    "framer": {"adapter": "claude_code_text", "model": "haiku"},
    "proposer_code": {"adapter": "claude_code_text", "model": "opus"},
    "proposer_codex": {"adapter": "codex_text", "model": "gpt-5.5"},
    "proposer_kimi": {
        "adapter": "claude_code_text_kimi",
        "model": "kimi-k2.6",
    },
    "proposer_deepseek": {
        "adapter": "claude_code_text_deepseek",
        "model": "deepseek-v4-pro",
    },
    "synthesizer": {"adapter": "claude_code_text", "model": "opus"},
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

    Lines go to stderr so stdout-only consumers (smoke harnesses,
    pipelines) are unaffected. The callback is thread-safe: orchestra
    fan-out workers may emit ``state_exit`` events from worker
    threads, and a module-level lock keeps line writes from
    interleaving.
    """
    import threading

    lock = threading.Lock()

    def _emit(line: str) -> None:
        with lock:
            sys.stderr.write(line + "\n")
            sys.stderr.flush()

    def callback(event: Any) -> None:
        kind = event.kind
        if kind == "fan_out_start":
            children = event.children or ()
            count = len(children)
            _emit(
                f"[duplo] fan_out start ({count} parallel proposers)"
            )
            return
        if kind == "fan_out_end":
            _emit("[duplo] fan_out end")
            return
        if kind not in ("state_enter", "state_exit"):
            return
        model = event.model or event.adapter or "transform"
        if kind == "state_enter":
            _emit(
                f"[duplo] state={event.state_name} model={model} "
                "status=running"
            )
            return
        elapsed = event.elapsed_seconds
        if elapsed is None:
            _emit(
                f"[duplo] state={event.state_name} model={model} "
                "status=complete"
            )
        else:
            _emit(
                f"[duplo] state={event.state_name} model={model} "
                f"elapsed={elapsed:.1f}s status=complete"
            )

    return callback

_TRUTHY = ("1", "true", "yes", "on")


class CouncilError(RuntimeError):
    """Raised when the council path cannot run or returns no plan."""


class CanonicalPlanFormatError(ValueError):
    """Raised when a synthesized canonical PLAN.md is not McLoop-executable.

    The canonical-mode plan body must contain at least one
    ``## Phase phase_NNN: title`` header (Slice C convention) and
    each phase must have at least one unchecked ``- [ ]`` task line.
    A plan body that fails either invariant is rejected fail-closed
    before duplo writes PLAN.md to disk; the upstream caller in
    ``duplo.planner.generate_phase_plan`` surfaces the error rather
    than handing McLoop a plan it cannot execute.

    This is the canonical-mode counterpart to Slice C's
    ``LineageValidationError`` (re-author mode). Both validators
    enforce contracts the synthesizer template documents but the
    schema cannot. The Slice D fswatch-run smoke is the empirical
    case that produced the need (4 phases, narrative prose, zero
    ``- [ ]`` lines, McLoop saw a plan it could not run); see
    ``orchestra/design/synthesizer-output-contract.md``'s
    "Workflow boundary" section for the structural rationale.
    """


# Canonical-mode plan body parsing. The header regex is permissive
# enough to detect any `## Phase <id>:` form (so the validator can
# tell users what they got wrong); a separate strict regex enforces
# the canonical phase_NNN form. Slice C's reauthor parser uses the
# permissive regex too -- shared across modes for cross-mode
# consistency.
_CANONICAL_PHASE_HEADER_RE = re.compile(
    r"^##\s+Phase\s+(?P<id>[A-Za-z0-9_]+):\s+(?P<title>.+?)\s*$"
)
_CANONICAL_TASK_LINE_RE = re.compile(r"^\s*-\s+\[\s*\]\s+\S")
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


def _validate_canonical_plan_markdown(
    plan_body: str,
    *,
    required_phase_id: str | None = None,
) -> None:
    """Fail-closed check that ``plan_body`` is McLoop-executable AND
    uses the supplied ``required_phase_id``.

    Invariants enforced:

      Check 1: the plan body has at least one
        ``## Phase phase_NNN: title`` header (permissive regex).

      Check 2: each phase section (the lines between consecutive
        phase headers, or from the final header to EOF) has at
        least one unchecked ``- [ ]`` task line, AND the total
        across the plan body is greater than zero.

      Check 3: every phase header's phase_id matches the strict
        canonical form ``^phase_\\d{3,}$``. Headers like
        ``## Phase Phase 1: ...`` or ``## Phase phase1: ...`` are
        rejected.

      Check 4: phase_ids are unique within the plan body.

      Check 5: when ``required_phase_id`` is supplied, the plan
        body MUST contain a phase header using exactly that id.

      Check 6: phase_ids are monotonically increasing in document
        order by their numeric suffix.

    All violations are accumulated; one raise surfaces the full
    picture so the synthesizer (or a debugging human) sees what to
    fix in one error message.
    """
    lines = plan_body.splitlines()
    headers: list[tuple[int, str, str]] = []
    for index, line in enumerate(lines):
        match = _CANONICAL_PHASE_HEADER_RE.match(line)
        if match is not None:
            headers.append(
                (index, match.group("id"), match.group("title"))
            )

    if not headers:
        raise CanonicalPlanFormatError(
            "synthesized PLAN.md has no `## Phase phase_NNN: title` "
            "headers; canonical mode requires the Slice C phase-id "
            "header form so McLoop can resolve task -> phase mappings"
        )

    errors: list[str] = []
    total_tasks = 0

    # Check 2 (per-phase tasks) + Check 1 task total accumulator.
    for idx, (line_idx, phase_id, title) in enumerate(headers):
        next_line_idx = (
            headers[idx + 1][0] if idx + 1 < len(headers) else len(lines)
        )
        section = lines[line_idx + 1 : next_line_idx]
        tasks = sum(1 for line in section if _CANONICAL_TASK_LINE_RE.match(line))
        total_tasks += tasks
        if tasks == 0:
            errors.append(
                f"phase {phase_id!r} ({title!r}) has no `- [ ]` task "
                "lines; mcloop cannot iterate a phase that contains "
                "only narrative prose"
            )

    if total_tasks == 0:
        errors.insert(
            0,
            "synthesized PLAN.md has zero `- [ ]` task lines across "
            f"{len(headers)} phase header(s); the canonical contract "
            "requires executable checklist tasks (the Slice D smoke "
            "regression case)",
        )

    # Check 3: strict phase_id format.
    nonstrict = [pid for _, pid, _ in headers if not _STRICT_PHASE_ID_RE.match(pid)]
    if nonstrict:
        errors.append(
            "phase header(s) with non-canonical phase_id (must match "
            "`^phase_\\d{3,}$`): " + ", ".join(repr(pid) for pid in nonstrict)
        )

    # Check 4: uniqueness.
    seen: dict[str, int] = {}
    duplicates: set[str] = set()
    for _, pid, _ in headers:
        seen[pid] = seen.get(pid, 0) + 1
        if seen[pid] > 1:
            duplicates.add(pid)
    if duplicates:
        errors.append(
            "duplicate phase_id(s) in synthesized plan body: "
            + ", ".join(sorted(repr(pid) for pid in duplicates))
        )

    # Check 5: required_phase_id present when supplied.
    if required_phase_id is not None:
        actual_ids = [pid for _, pid, _ in headers]
        if required_phase_id not in actual_ids:
            errors.append(
                f"required_phase_id {required_phase_id!r} not present in "
                "synthesized plan body; synthesizer emitted "
                f"{actual_ids!r} instead. The runtime computes the "
                "phase_id deterministically from the existing PLAN.md; "
                "the synthesizer must use it verbatim."
            )

    # Check 6: monotonic order by numeric suffix (skip when any id is
    # non-strict; the Check 3 message will already be on the error
    # list, and pretending we know an order over malformed ids would
    # produce confusing error messages).
    suffixes: list[int] = []
    have_all_strict = True
    for _, pid, _ in headers:
        s = _phase_id_numeric_suffix(pid)
        if s is None:
            have_all_strict = False
            break
        suffixes.append(s)
    if have_all_strict:
        out_of_order = [
            (headers[i][1], headers[i + 1][1])
            for i in range(len(suffixes) - 1)
            if suffixes[i] >= suffixes[i + 1]
        ]
        if out_of_order:
            errors.append(
                "phase_ids out of order in document (must be strictly "
                "monotonically increasing): "
                + ", ".join(f"{a!r} -> {b!r}" for a, b in out_of_order)
            )

    if errors:
        raise CanonicalPlanFormatError("; ".join(errors))


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


def author_phase_plan(
    *,
    prompt: str,
    system: str,
    phase_num: int | str,
    project_dir: Path | None = None,
) -> str:
    """Run ``council_four`` and return the synthesizer's plan body.

    ``prompt`` and ``system`` are the same strings the legacy
    ``query()`` path would send: prompt is the rendered reference
    material body, system is the planner's _PHASE_SYSTEM directive.
    They are concatenated into the council's ``state`` external
    input. ``question`` is fixed by template at this layer
    (canonical Duplo phase authoring); when Plan Ledger lands and
    re-authoring becomes a real path, the question shape will be
    revisited.

    Raises ``CouncilError`` on terminal != "done", missing plan
    artifact, or import-time failures.
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
    question = (
        f"Author the Phase {phase_num} plan from the reference "
        "material above."
    )
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
        raise CouncilError(
            f"council_four_canonical invocation failed: {exc}"
        ) from exc
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
        raise CouncilError(
            "council_four_canonical accepted but produced no 'plan' artifact"
        )
    plan_text: str = plan_view.value.strip()
    if not plan_text:
        raise CouncilError(
            "council_four_canonical accepted but the 'plan' artifact is empty"
        )

    _write_audit(
        audits_root / result.run_id,
        result=result,
        question=question,
        elapsed_s=elapsed_s,
    )

    # Canonical-mode format validator: the McLoop consumer expects
    # phase headers in `## Phase phase_NNN: title` form and at least
    # one `- [ ]` task line per phase. The validator also enforces
    # the required_phase_id constraint computed above (Check 5),
    # phase_id uniqueness (Check 4), strict format (Check 3), and
    # monotonic order (Check 6). Audit dir is already written for
    # debugging; PLAN.md is NOT written by author_phase_plan itself
    # (the caller in duplo.planner does that), so a raise here
    # means PLAN.md stays untouched.
    _validate_canonical_plan_markdown(
        plan_text, required_phase_id=required_phase_id
    )

    return plan_text


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
       file). Loaded from its parent dir.
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
            raise CouncilError(
                f"DUPLO_COUNCIL_CONFIG path does not exist: {explicit_path}"
            )
        try:
            cfg = load_config(project_dir=explicit_path.parent)
        except config_error as exc:
            raise CouncilError(
                f"failed to load council config at {explicit_path}: {exc}"
            ) from exc
        return _ensure_council_workflow(
            cfg, config_cls=config_cls, workflow_cls=workflow_cls
        )

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


def _ensure_council_workflow(
    cfg: Any, *, config_cls: Any, workflow_cls: Any
) -> Any:
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
        body = view.value if isinstance(view.value, str) else json.dumps(
            view.value, indent=2, sort_keys=True
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
        verdict_path.write_text(
            json.dumps(verdict_view.value, indent=2, sort_keys=True)
        )

    run_meta = {
        "run_id": result.run_id,
        "terminal": result.terminal,
        "elapsed_s": round(elapsed_s, 2),
        "question": question,
        "log_path": str(result.log_path),
    }
    (audit_dir / "run_meta.json").write_text(
        json.dumps(run_meta, indent=2, sort_keys=True)
    )
