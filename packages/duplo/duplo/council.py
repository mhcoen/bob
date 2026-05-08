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
import sys
import time
from pathlib import Path
from typing import Any

from collections.abc import Callable

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
    inputs: dict[str, Any] = {
        "state": state_text,
        "question": question,
        "ledger_slice": "",
        "design_context": "",
    }

    audits_root = project_dir / ".duplo" / "audits" / "council"
    audits_root.mkdir(parents=True, exist_ok=True)
    started_at = time.time()
    try:
        result = run_workflow(
            "council_four",
            inputs,
            cfg,
            project_dir=project_dir,
            data_root=audits_root / "_runs",
            progress_callback=make_duplo_progress_callback(),
        )
    except Exception as exc:  # noqa: BLE001 — surface any wiring failure
        raise CouncilError(
            f"council_four invocation failed: {exc}"
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
            f"council_four did not accept (terminal={result.terminal!r}, "
            f"decision={decision!r}). Feedback: {feedback!r}. "
            f"Run audit at {audits_root / result.run_id}"
        )

    plan_view = result.artifacts.get("plan")
    if plan_view is None or not isinstance(plan_view.value, str):
        raise CouncilError(
            "council_four accepted but produced no 'plan' artifact"
        )
    plan_text: str = plan_view.value.strip()
    if not plan_text:
        raise CouncilError(
            "council_four accepted but the 'plan' artifact is empty"
        )

    _write_audit(
        audits_root / result.run_id,
        result=result,
        question=question,
        elapsed_s=elapsed_s,
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


def _ensure_council_workflow(
    cfg: Any, *, config_cls: Any, workflow_cls: Any
) -> Any:
    if "council_four" in cfg.workflows:
        return cfg
    new_workflows = dict(cfg.workflows)
    new_workflows["council_four"] = workflow_cls(pattern="council_four")
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
    workflows = {"council_four": workflow_cls(pattern="council_four")}
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
