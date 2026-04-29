"""Code-edit wrapper interface.

Defines the boundary between mcloop and orchestra at the level of one
edit attempt. Mcloop's outer loop (retry, rate-limit detection,
success classification, session context updates, Telegram approval,
PID tracking, watchdog) is unchanged. The single inner edit
invocation now goes through ``invoke_code_edit``, which dispatches to
either the direct backend (the body lifted from ``runner.run_task``)
or the orchestra backend (a call to ``orchestra.run_workflow`` with
the configured pattern).

Backend selection reads ``<project_dir>/.orchestra/config.json``. If
the file is absent, malformed, references an unknown workflow, or if
``workflows.code_edit.pattern == "direct"``, the direct backend
applies. Otherwise orchestra runs. Any exception during selection
falls back to direct so a misconfigured project does not lose the
working default.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcloop import runner as _runner
from mcloop.runner import DEFAULT_TASK_TIMEOUT


def _orchestra_warn(msg: str) -> None:
    """Emit a single-line warning about an orchestra fallback to direct.

    Goes to stderr so it surfaces in mcloop's run output without
    blocking. The same prefix is used by the runner's allowed-tools
    bypass warning so a user grepping logs sees both in one place.
    """
    print(f"[orchestra] falling back to direct backend due to error: {msg}", file=sys.stderr)


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
    """Return ``"direct"`` or ``"orchestra"`` per the project config.

    Silent fallbacks (legitimate opt-outs):
    - ``.orchestra/config.json`` is missing
    - the JSON does not parse (the user may be mid-edit, or have a stub)
    - the ``code_edit`` workflow entry is absent
    - ``workflows.code_edit.pattern == "direct"`` (sentinel)

    Loud fallbacks (warn to stderr, then still fall back so production
    keeps working): orchestra import error after the user opted in,
    role binding errors, and any other exception loading the config.
    """
    return _select_backend_for(project_dir, "code_edit")


def _select_backend_for(project_dir: Path, workflow_name: str) -> str:
    config_path = project_dir / ".orchestra" / "config.json"
    if not config_path.exists():
        return "direct"
    try:
        json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "direct"
    try:
        from orchestra.config import load_config
    except ImportError as exc:
        _orchestra_warn(f"orchestra not importable: {exc}")
        return "direct"
    try:
        cfg = load_config(project_dir)
    except Exception as exc:
        _orchestra_warn(f"failed to load .orchestra/config.json: {exc}")
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
) -> CodeEditResult:
    """Perform one code-edit attempt and return a structured result.

    Mcloop owns the outer loop; this function owns one inner edit. It
    selects the backend per the project config and dispatches.
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
        )
    elif is_bug_task:
        prompt = _runner._build_bug_task_prompt(
            instruction,
            description,
            task_label,
            context,
            check_commands,
            eliminated,
        )
    else:
        prompt = _runner._build_normal_prompt(
            instruction,
            description,
            task_label,
            context,
            check_commands,
            eliminated,
        )
    session_env = _runner._build_session_env(task_label=task_label, cli="claude")
    cmd = _runner._build_command("claude", prompt, env=session_env, model=model)
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
    session_env = _runner._build_session_env(task_label="bug-verify", cli="claude")
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
) -> CodeEditResult:
    from orchestra import run_workflow
    from orchestra.config import load_config

    cfg = load_config(project_dir)
    inputs: dict[str, Any] = {
        "instruction": instruction,
        "context": context,
        "prior_errors": prior_errors,
        "eliminated": list(eliminated or []),
        "project_dir": str(project_dir),
        "description": description,
        "task_label": task_label,
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
            f"{exc}. Configure workflows.bug_verify.pattern = \"direct\" "
            "in .orchestra/config.json to opt out, or wait for the "
            "orchestra-side bug_verify workflow to land."
        ) from exc
    return _orchestra_to_code_edit_result(result, fallback_log=log_dir)


def _orchestra_to_code_edit_result(
    result: Any, *, fallback_log: Path
) -> CodeEditResult:
    summary = result.summary or {}
    output = summary.get("output", "")
    if not isinstance(output, str):
        output = ""
    exit_code_raw = summary.get(
        "exit_code", 1 if result.terminal != "done" else 0
    )
    try:
        exit_code = int(exit_code_raw)
    except (TypeError, ValueError):
        exit_code = 1
    changed_files_raw = summary.get("changed_files") or []
    changed_files = (
        [str(p) for p in changed_files_raw]
        if isinstance(changed_files_raw, list)
        else []
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
