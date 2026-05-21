"""Code-edit wrapper interface.

Defines the boundary between mcloop and orchestra at the level of one
edit attempt. Mcloop's outer loop (retry, rate-limit detection,
success classification, session context updates, Telegram approval,
PID tracking, watchdog) is unchanged. The single inner edit
invocation now goes through ``invoke_code_edit``, which dispatches to
either the direct backend (the body lifted from ``runner.run_task``)
or the orchestra backend (a call to ``orchestra.run_workflow`` with
the configured pattern).

Backend selection reads the merged orchestra config returned by
``orchestra.config.load_config(project_dir)``. The merged view is
``~/.orchestra/config.json`` (the canonical source) overlaid with an
optional ``<project_dir>/.orchestra/config.json``. The orchestra
backend dispatches whenever the merged config has the named workflow
set to a pattern other than ``direct``. The direct backend applies
when the workflow is missing from the merged config or its pattern is
``direct``. Any exception during selection falls back to direct so a
misconfigured environment does not lose the working default. When a
project-local override file is present and not yet acknowledged via
``mcloop ack-orchestra-override``, mcloop emits a multi-line banner
to stderr so the user notices their project is overriding the global
config. See ``mcloop.orchestra_override`` for the ack mechanism.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcloop import runner as _runner
from mcloop.orchestra_override import (
    banner_lines,
    is_acknowledged,
    project_orchestra_config_path,
)
from mcloop.runner import DEFAULT_TASK_TIMEOUT

# Module-level latch so the project-local override banner fires at
# most once per mcloop process, no matter how many edit attempts the
# outer loop makes.
_PROJECT_OVERRIDE_NOTE_EMITTED = False


def _orchestra_warn(msg: str) -> None:
    """Emit a single-line warning about an orchestra fallback to direct.

    Goes to stderr so it surfaces in mcloop's run output without
    blocking. The same prefix is used by the runner's allowed-tools
    bypass warning so a user grepping logs sees both in one place.
    """
    print(f"[orchestra] falling back to direct backend due to error: {msg}", file=sys.stderr)


def _maybe_emit_project_override_note(project_dir: Path) -> None:
    """If ``project_dir/.orchestra/config.json`` exists and the user has
    not acknowledged it, emit the multi-line override banner to stderr.

    Fires regardless of whether orchestra ends up dispatched. The user
    is informed that their project is shadowing the global config in
    case they did not intend to. Latched at module scope so the message
    appears once per mcloop process even when the outer loop calls
    ``_select_backend`` repeatedly.

    The banner is suppressed when ``<project>/.mcloop/orchestra-override-ack``
    holds the sha256 fingerprint of the current local config bytes. An
    edit to the local config invalidates the ack and the banner returns
    until the user re-runs ``mcloop ack-orchestra-override``.
    """
    global _PROJECT_OVERRIDE_NOTE_EMITTED
    if _PROJECT_OVERRIDE_NOTE_EMITTED:
        return
    config_path = project_orchestra_config_path(project_dir)
    if not config_path.is_file():
        return
    if is_acknowledged(project_dir, config_path):
        # User explicitly acknowledged this exact override. Latch
        # anyway so a later edit-then-recheck path still hits the
        # ack-aware logic only once per process.
        _PROJECT_OVERRIDE_NOTE_EMITTED = True
        return
    for line in banner_lines(config_path):
        print(line, file=sys.stderr)
    _PROJECT_OVERRIDE_NOTE_EMITTED = True


@dataclass
class CodeEditResult:
    """Result of one code-edit attempt.

    Mirrors ``RunResult`` plus the orchestra-only fields the wrapper
    surfaces back to the caller (``changed_files``, ``summary``).
    Direct-backend results carry an empty ``changed_files`` list and
    ``summary = None``; orchestra-backend results carry both.
    """

    success: bool
    output: str
    exit_code: int
    log_path: Path
    changed_files: list[str]
    summary: dict[str, Any] | None


def _select_backend(project_dir: Path) -> str:
    """Return ``"direct"`` or ``"orchestra"`` per the merged orchestra
    config.

    The selection reads the merged view of ``~/.orchestra/config.json``
    plus an optional ``<project_dir>/.orchestra/config.json``. The
    global config is the canonical source; the project-local file is an
    advanced override that knowledgeable users add deliberately.

    Silent fallbacks (legitimate opt-outs):
    - the merged config does not declare a ``code_edit`` workflow
    - ``workflows.code_edit.pattern == "direct"`` (sentinel)

    Loud fallbacks (warn to stderr, then still fall back so production
    keeps working): orchestra import error after the user opted in,
    role binding errors, and any other exception loading the merged
    config.

    Side effect: when a project-local file is present, emit a one-time
    note that the project is overriding the global config.
    """
    return _select_backend_for(project_dir, "code_edit")


def _select_backend_for(project_dir: Path, workflow_name: str) -> str:
    _maybe_emit_project_override_note(project_dir)
    try:
        from orchestra.config import (
            global_config_path,
            load_config,
            project_config_path,
        )
    except ImportError as exc:
        _orchestra_warn(f"orchestra not importable: {exc}")
        return "direct"
    # Distinguish "user has not set up orchestra at all" from "user has
    # configured orchestra but did not declare this workflow". orchestra
    # load_config falls back to default_config() when neither file is
    # present, and default_config() declares a code_edit workflow with
    # pattern == "single". Without this guard, every project on a
    # machine that has never created an orchestra config would silently
    # route through orchestra. The user's stated design is that the
    # global config is the single canonical surface; absence of any
    # config file means orchestra is not configured, so direct applies.
    if not global_config_path().is_file() and not project_config_path(project_dir).is_file():
        return "direct"
    try:
        cfg = load_config(project_dir)
    except Exception as exc:
        _orchestra_warn(f"failed to load orchestra config: {exc}")
        return "direct"
    if workflow_name not in cfg.workflows:
        return "direct"
    try:
        wf = cfg.workflow(workflow_name)
    except Exception as exc:
        _orchestra_warn(f"failed to read workflow {workflow_name!r}: {exc}")
        return "direct"
    if wf.pattern == "direct":
        return "direct"
    return "orchestra"


def invoke_code_edit(
    instruction: str,
    context: str,
    prior_errors: str,
    eliminated: list[str],
    project_dir: Path,
    log_dir: Path,
    description: str,
    task_label: str,
    check_commands: list[str] | None,
    is_bug_task: bool,
    model: str | None,
    timeout: int = DEFAULT_TASK_TIMEOUT,
    task_id: str = "",
) -> CodeEditResult:
    """Perform one code-edit attempt and return a structured result.

    Mcloop owns the outer loop; this function owns one inner edit. It
    selects the backend per the project config and dispatches.

    ``task_id`` is the R4 canonical task identifier woven into the
    visible ``Task: …`` prompt line and the persisted log header.
    """
    project_dir = Path(project_dir)
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    backend = _select_backend(project_dir)
    if backend == "orchestra":
        return _invoke_orchestra(
            instruction=instruction,
            context=context,
            prior_errors=prior_errors,
            eliminated=eliminated,
            project_dir=project_dir,
            log_dir=log_dir,
            description=description,
            task_label=task_label,
            check_commands=check_commands,
            is_bug_task=is_bug_task,
            model=model,
            timeout=timeout,
            task_id=task_id,
        )
    return _invoke_direct(
        instruction=instruction,
        context=context,
        prior_errors=prior_errors,
        eliminated=eliminated,
        project_dir=project_dir,
        log_dir=log_dir,
        description=description,
        task_label=task_label,
        check_commands=check_commands,
        is_bug_task=is_bug_task,
        model=model,
        timeout=timeout,
        task_id=task_id,
    )


def invoke_bug_verify(
    bugs_content: str,
    project_dir: Path,
    log_dir: Path,
    model: str | None = None,
    timeout: int = DEFAULT_TASK_TIMEOUT,
) -> CodeEditResult:
    """Perform one bug-verify session and return a structured result.

    Same wrapper shape as ``invoke_code_edit`` but for the read-only
    bug-verification workflow. The orchestra-backed variant is wired
    but not yet exercisable: orchestra does not ship a ``bug_verify``
    workflow file. Until that lands, the orchestra path raises a
    clear error and the direct path remains the working default.
    """
    project_dir = Path(project_dir)
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    backend = _select_bug_verify_backend(project_dir)
    if backend == "orchestra":
        return _invoke_bug_verify_orchestra(
            bugs_content=bugs_content,
            project_dir=project_dir,
            log_dir=log_dir,
            model=model,
            timeout=timeout,
        )
    return _invoke_bug_verify_direct(
        bugs_content=bugs_content,
        project_dir=project_dir,
        log_dir=log_dir,
        model=model,
        timeout=timeout,
    )


def _select_bug_verify_backend(project_dir: Path) -> str:
    return _select_backend_for(project_dir, "bug_verify")


# --------------------------------------------------------------------
# Direct backend: lifted from runner.run_task
# --------------------------------------------------------------------


def _invoke_direct(
    *,
    instruction: str,
    context: str,
    prior_errors: str,
    eliminated: list[str],
    project_dir: Path,
    log_dir: Path,
    description: str,
    task_label: str,
    check_commands: list[str] | None,
    is_bug_task: bool,
    model: str | None,
    timeout: int,
    task_id: str = "",
) -> CodeEditResult:
    if prior_errors:
        prompt = _runner._build_bug_prompt(
            instruction,
            description,
            task_label,
            context,
            check_commands,
            prior_errors,
            eliminated,
            task_id=task_id,
        )
    elif is_bug_task:
        prompt = _runner._build_bug_task_prompt(
            instruction,
            description,
            task_label,
            context,
            check_commands,
            eliminated,
            task_id=task_id,
        )
    else:
        prompt = _runner._build_normal_prompt(
            instruction,
            description,
            task_label,
            context,
            check_commands,
            eliminated,
            task_id=task_id,
        )
    session_env = _runner._build_session_env(task_label=task_label, cli="claude")
    cmd = _runner._build_command("claude", prompt, env=session_env, model=model)
    _runner.ensure_subscription_preflight(
        cli="claude",
        model=model,
        env=session_env,
        cwd=project_dir,
    )
    output, returncode = _runner._run_session(
        cmd,
        project_dir,
        env=session_env,
        timeout=timeout,
    )
    log_path = _runner._write_log(
        log_dir,
        instruction,
        cmd,
        output,
        returncode,
        task_id=task_id,
    )
    return CodeEditResult(
        success=returncode == 0,
        output=output,
        exit_code=returncode,
        log_path=log_path,
        changed_files=_detect_changed_files(project_dir),
        summary=None,
    )


def _detect_changed_files(project_dir: Path) -> list[str]:
    """Return the files git reports as modified or new in ``project_dir``.

    Mirrors orchestra's ``_detect_changed_files`` so both backends
    populate the same shape. Best-effort: if the project is not a git
    repo or git is missing, returns an empty list. Does not raise.
    """
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    files: list[str] = []
    for line in out.stdout.splitlines():
        path = line[3:].strip()
        if path:
            files.append(path)
    return files


def _invoke_bug_verify_direct(
    *,
    bugs_content: str,
    project_dir: Path,
    log_dir: Path,
    model: str | None,
    timeout: int,
) -> CodeEditResult:
    """Direct bug-verify path.

    Builds a session env up front and threads it through
    ``_build_command`` and ``_run_session`` so ``_apply_provider_env``
    fires for third-party model aliases (kimi-k2.6, DeepSeek, any
    fully qualified provider slug). The legacy ``run_bug_verify``
    body skipped this and consequently routed third-party models to
    the wrong endpoint. The code-edit direct backend already does the
    right thing; this function now mirrors it.
    """
    from mcloop.prompts import build_bug_verify_prompt

    prompt = build_bug_verify_prompt(bugs_content)
    # Match the legacy run_bug_verify env exactly: no task_label, so
    # MCLOOP_TASK_LABEL stays unset for native Anthropic models. Only
    # the third-party provider routing keys differ from the legacy
    # path, and only when the model is a third-party alias.
    session_env = _runner._build_session_env(task_label="", cli="claude")
    cmd = _runner._build_command("claude", prompt=prompt, env=session_env, model=model)
    output, returncode = _runner._run_session(
        cmd,
        project_dir,
        env=session_env,
        timeout=timeout,
    )
    log_path = _runner._write_log(
        log_dir,
        "bug-verify",
        cmd,
        output,
        returncode,
    )
    return CodeEditResult(
        success=returncode == 0,
        output=output,
        exit_code=returncode,
        log_path=log_path,
        changed_files=_detect_changed_files(project_dir),
        summary=None,
    )


# --------------------------------------------------------------------
# Orchestra backend
# --------------------------------------------------------------------


def _invoke_orchestra(
    *,
    instruction: str,
    context: str,
    prior_errors: str,
    eliminated: list[str],
    project_dir: Path,
    log_dir: Path,
    description: str,
    task_label: str,
    check_commands: list[str] | None,
    is_bug_task: bool,
    model: str | None,
    timeout: int,
    task_id: str = "",
) -> CodeEditResult:
    from orchestra import run_workflow
    from orchestra.config import load_config

    cfg = load_config(project_dir)
    _ensure_orchestra_subscription_preflight(
        cfg=cfg,
        model=model,
        task_label=task_label,
        project_dir=project_dir,
    )
    inputs: dict[str, Any] = {
        "instruction": instruction,
        "context": context,
        "prior_errors": prior_errors,
        "eliminated": list(eliminated or []),
        "project_dir": str(project_dir),
        "description": description,
        "task_label": task_label,
        "task_id": task_id,
        "check_commands": list(check_commands) if check_commands else [],
        "is_bug_task": bool(is_bug_task),
    }
    invocation_options: dict[str, Any] = {
        "log_dir": str(log_dir),
        "timeout": timeout,
        "project_dir": str(project_dir),
    }
    if model is not None:
        invocation_options["model"] = model
    result = run_workflow(
        "code_edit",
        inputs,
        cfg,
        invocation_options=invocation_options,
        project_dir=project_dir,
        data_root=log_dir / "orchestra-runs",
    )
    return _orchestra_to_code_edit_result(result, fallback_log=log_dir)


def _ensure_orchestra_subscription_preflight(
    *,
    cfg: Any,
    model: str | None,
    task_label: str,
    project_dir: Path,
) -> None:
    """Run the Claude subscription preflight for orchestra-backed code edits.

    Orchestra owns the subprocess env for this backend, so use its env
    builder instead of approximating the direct runner path. If mcloop
    supplied a model override, orchestra applies that model to every
    state; otherwise use the configured editor role when present.
    """
    try:
        from orchestra.adapters._subprocess import build_session_env
    except ImportError:
        return

    preflight_model = model
    if preflight_model is None:
        binding = getattr(cfg, "roles", {}).get("editor")
        adapter = getattr(binding, "adapter", "") if binding is not None else ""
        if adapter.startswith("claude_code"):
            preflight_model = getattr(binding, "model", None)
    env = build_session_env(
        task_label=task_label,
        cli="claude",
        model=preflight_model,
    )
    _runner.ensure_subscription_preflight(
        cli="claude",
        model=preflight_model,
        env=env,
        cwd=project_dir,
    )


def _invoke_bug_verify_orchestra(
    *,
    bugs_content: str,
    project_dir: Path,
    log_dir: Path,
    model: str | None,
    timeout: int,
) -> CodeEditResult:
    from orchestra import run_workflow
    from orchestra.config import load_config

    cfg = load_config(project_dir)
    inputs: dict[str, Any] = {"bugs_content": bugs_content}
    invocation_options: dict[str, Any] = {
        "log_dir": str(log_dir),
        "timeout": timeout,
        "project_dir": str(project_dir),
    }
    if model is not None:
        invocation_options["model"] = model
    try:
        result = run_workflow(
            "bug_verify",
            inputs,
            cfg,
            invocation_options=invocation_options,
            project_dir=project_dir,
            data_root=log_dir / "orchestra-runs",
        )
    except Exception as exc:
        raise RuntimeError(
            "orchestra bug_verify workflow not available: "
            f'{exc}. Configure workflows.bug_verify.pattern = "direct" '
            "in .orchestra/config.json to opt out, or wait for the "
            "orchestra-side bug_verify workflow to land."
        ) from exc
    return _orchestra_to_code_edit_result(result, fallback_log=log_dir)


def _orchestra_to_code_edit_result(result: Any, *, fallback_log: Path) -> CodeEditResult:
    summary = result.summary or {}
    output = summary.get("output", "")
    if not isinstance(output, str):
        output = ""
    exit_code_raw = summary.get("exit_code", 1 if result.terminal != "done" else 0)
    try:
        exit_code = int(exit_code_raw)
    except (TypeError, ValueError):
        exit_code = 1
    changed_files_raw = summary.get("changed_files") or []
    changed_files = (
        [str(p) for p in changed_files_raw] if isinstance(changed_files_raw, list) else []
    )
    adapter_log = summary.get("adapter_log")
    if isinstance(adapter_log, str) and adapter_log:
        log_path = Path(adapter_log)
    elif result.log_path:
        log_path = Path(result.log_path)
    else:
        log_path = fallback_log
    success = result.terminal == "done" and exit_code == 0
    return CodeEditResult(
        success=success,
        output=output,
        exit_code=exit_code,
        log_path=log_path,
        changed_files=changed_files,
        summary=dict(summary),
    )
