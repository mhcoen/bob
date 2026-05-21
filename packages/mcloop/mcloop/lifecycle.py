"""Process lifecycle: interrupt state, orphan cleanup, active process management."""

from __future__ import annotations

import atexit
import json as _json
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcloop import formatting
from mcloop._planfile_compat import (
    CHECKBOX_RE,
    Task,
    mark_failed,
    parse,
)
from mcloop.formatting import format_elapsed

try:
    from orchestra.adapters._subprocess import (
        clear_active_process as _orchestra_clear_active_process,
    )
    from orchestra.adapters._subprocess import (
        get_active_process as _orchestra_get_active_process,
    )
    from orchestra.adapters._subprocess import set_interrupted as _orchestra_set_interrupted
except ImportError:
    _orchestra_clear_active_process = None
    _orchestra_get_active_process = None
    _orchestra_set_interrupted = None

# Phase tracking for interrupt state capture
_current_phase = ""  # task, checks, audit, user_prompt
_current_task_label = ""
_current_task_text = ""
_current_task_id = ""  # R4: canonical T-NNNNNN id; "" when absent
_phase_start_time = 0.0
_project_dir: Path | None = None
_lifecycle_state = "not_started"
_atexit_callback_registered = False
_TASK_ID_RE = re.compile(r"^(T-\d{6}):\s*(.*)$")


def register_atexit_cleanup() -> None:
    """Register process cleanup exactly once for interpreter shutdown."""
    global _atexit_callback_registered, _lifecycle_state
    if _atexit_callback_registered:
        return
    atexit.register(_atexit_shutdown)
    _atexit_callback_registered = True
    _lifecycle_state = "running"


def unregister_atexit_cleanup() -> None:
    """Remove the lifecycle atexit cleanup hook if it was registered."""
    global _atexit_callback_registered
    if not _atexit_callback_registered:
        return
    atexit.unregister(_atexit_shutdown)
    _atexit_callback_registered = False


def shutdown_lifecycle() -> None:
    """Explicitly shut down lifecycle resources.

    Explicit shutdown is diagnostic: unexpected lifecycle cleanup errors
    are allowed to propagate to the caller while logging and stderr are
    still alive. The atexit wrapper below is narrower and avoids late
    imports during interpreter teardown.
    """
    _shutdown_lifecycle(from_atexit=False)


def _atexit_shutdown() -> None:
    """Best-effort interpreter-teardown cleanup.

    This path must not import orchestra. Importing new modules from an
    atexit callback can trip Python's own shutdown ordering, notably
    ``threading._register_atexit`` via ``concurrent.futures``.
    """
    _shutdown_lifecycle(from_atexit=True)


def _shutdown_lifecycle(*, from_atexit: bool) -> None:
    global _lifecycle_state
    if _lifecycle_state in {"stopping", "stopped"}:
        return
    _lifecycle_state = "stopping"
    try:
        _kill_active_process(
            allow_orchestra_import=not from_atexit,
            propagate_orchestra_errors=not from_atexit,
        )
    finally:
        _lifecycle_state = "stopped"
        if not from_atexit:
            unregister_atexit_cleanup()


def _save_interrupt_state() -> None:
    """Write .mcloop/interrupted.json with current state.

    Called from the signal handler. Uses only synchronous file
    I/O and module-level state. No API calls.
    """
    import mcloop.runner as _runner

    if _project_dir is None:
        return
    mcloop_dir = _project_dir / ".mcloop"
    mcloop_dir.mkdir(exist_ok=True)
    elapsed = time.monotonic() - _phase_start_time if _phase_start_time else 0
    last_lines = list(_runner._last_output_lines)
    state = {
        "task_label": _current_task_label,
        "task_text": _current_task_text,
        "task_id": _current_task_id,
        "phase": _current_phase,
        "elapsed_seconds": round(elapsed, 1),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "last_output": last_lines,
    }
    try:
        (mcloop_dir / "interrupted.json").write_text(_json.dumps(state, indent=2) + "\n")
    except OSError:
        pass


def _unlink_active_pid_file() -> None:
    """Remove .mcloop/active-pid after the signal handler has killed
    the active subprocess group. The file is mcloop's record of an
    in-flight inner CLI subprocess; once we have killed it, the file
    no longer refers to anything live and should not persist on disk
    to be detected by the orphan-cleanup path on next startup. The
    orphan cleanup remains the safety net for unclean death (kill -9,
    OOM, power loss); this helper handles the clean-interrupt case.
    """
    if _project_dir is None:
        return
    pid_file = _project_dir / ".mcloop" / "active-pid"
    try:
        pid_file.unlink(missing_ok=True)
    except OSError:
        pass


def _check_interrupted(
    project_dir: Path,
    checklist_path: Path,
    active_paths: list[Path] | None = None,
) -> str | None:
    """Check for interrupted.json and prompt the user.

    ``active_paths`` is a priority-ordered list of files that may
    contain the interrupted task (e.g. BUGS.md, CURRENT_PLAN.md,
    PLAN.md). The skip/describe actions mutate the first file that
    contains the task as unchecked. When omitted, falls back to
    ``[checklist_path]`` for backward compatibility.

    Returns:
        "retry" to proceed normally
        "skip" to mark task [!] and move on
        "quit" to exit
        None if no interrupted state found
    """
    state_file = project_dir / ".mcloop" / "interrupted.json"
    if not state_file.exists():
        return None
    try:
        state = _json.loads(state_file.read_text())
    except (OSError, _json.JSONDecodeError):
        state_file.unlink(missing_ok=True)
        return None
    search_paths = active_paths if active_paths else [checklist_path]

    phase = state.get("phase", "task")
    label = state.get("task_label", "?")
    text = state.get("task_text", "unknown")
    elapsed = state.get("elapsed_seconds", 0)
    last_output = state.get("last_output", [])
    timestamp = state.get("timestamp", "")

    print(
        formatting.summary_header(),
        flush=True,
    )
    print(
        f"  Previous run was interrupted during {phase} phase ({timestamp})",
        flush=True,
    )
    print(
        f"  Task {label}: {text}",
        flush=True,
    )
    print(
        f"  Running for {format_elapsed(elapsed)}",
        flush=True,
    )
    if last_output:
        print("  Last output:", flush=True)
        for line in last_output[-5:]:
            print(f"    {line}", flush=True)
    print(
        formatting.summary_footer(),
        flush=True,
    )

    if phase == "user_prompt":
        print(
            "  Resuming where you left off.",
            flush=True,
        )
        state_file.unlink(missing_ok=True)
        return "retry"

    if phase == "audit":
        print(
            "  (r)esume audit / (s)kip audit / (q)uit",
            flush=True,
        )
    else:
        print(
            "  (r)etry / (d)escribe what went wrong / (s)kip / (q)uit",
            flush=True,
        )

    try:
        choice = input("  > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        choice = "q"

    if choice == "q":
        state_file.unlink(missing_ok=True)
        print("Exiting.", flush=True)
        sys.exit(0)

    if choice == "s":
        # Mark task as failed in the first split-plan file that contains
        # it as unchecked. The master PLAN.md is only consulted as a
        # fallback; marking [!] there has no effect because run_loop
        # reads from CURRENT_PLAN.md / BUGS.md.
        for p in search_paths:
            if not p.exists():
                continue
            tasks = parse(p)
            found = False
            for t in _all_tasks(tasks):
                if t.text.strip() == text.strip() and not t.checked:
                    mark_failed(p, t)
                    found = True
                    break
            if found:
                break
        state_file.unlink(missing_ok=True)
        return "skip"

    if choice == "d" and phase != "audit":
        print(
            "  Describe what went wrong (press Enter twice to finish):",
            flush=True,
        )
        lines: list[str] = []
        try:
            while True:
                line = input()
                if line == "":
                    break
                lines.append(line)
        except (EOFError, KeyboardInterrupt):
            pass
        description = " ".join(lines).strip()
        if description:
            # Write [RULEDOUT] under the task in whichever split-plan
            # file contains it. Falls back to checklist_path.
            target_path: Path | None = None
            for p in search_paths:
                if not p.exists():
                    continue
                tasks = parse(p)
                for t in _all_tasks(tasks):
                    if t.text.strip() == text.strip():
                        target_path = p
                        break
                if target_path is not None:
                    break
            if target_path is None:
                target_path = checklist_path
            _write_ruledout_to_plan(
                target_path,
                text,
                description,
            )
            _write_eliminated_json(
                project_dir,
                label,
                description,
            )
            print(
                f"  Recorded: [RULEDOUT] {description}",
                flush=True,
            )
        state_file.unlink(missing_ok=True)
        return "retry"

    # Default: retry
    state_file.unlink(missing_ok=True)
    return "retry"


def _all_tasks(tasks: list[Task]) -> list[Task]:
    """Flatten the task tree into a list."""
    result: list[Task] = []
    for t in tasks:
        result.append(t)
        result.extend(_all_tasks(t.children))
    return result


def _checkbox_task_text(line: str) -> str | None:
    """Return checkbox task text without a leading canonical task id."""
    match = CHECKBOX_RE.match(line)
    if match is None:
        return None
    text = match.group(3).strip()
    id_match = _TASK_ID_RE.match(text)
    if id_match is None:
        return text
    return id_match.group(2).strip()


def _write_ruledout_to_plan(
    checklist_path: Path,
    task_text: str,
    description: str,
) -> None:
    """Append a [RULEDOUT] line under a task in PLAN.md."""
    lines = checklist_path.read_text().splitlines()
    for i, line in enumerate(lines):
        m = CHECKBOX_RE.match(line)
        if m and _checkbox_task_text(line) == task_text.strip():
            indent = len(m.group(1))
            ruledout_line = " " * (indent + 2) + f"[RULEDOUT] {description}"
            lines.insert(i + 1, ruledout_line)
            checklist_path.write_text("\n".join(lines) + "\n")
            return


def _write_eliminated_json(
    project_dir: Path,
    task_label: str,
    description: str,
) -> None:
    """Append an entry to .mcloop/eliminated.json."""
    elim_path = project_dir / ".mcloop" / "eliminated.json"
    try:
        data = _json.loads(elim_path.read_text())
    except (OSError, _json.JSONDecodeError):
        data = {}
    if task_label not in data:
        data[task_label] = []
    data[task_label].append(
        {
            "approach": description,
            "timestamp": time.strftime("%Y-%m-%d"),
        }
    )
    elim_path.write_text(_json.dumps(data, indent=2) + "\n")


def _kill_orphan_sessions(project_dir: Path) -> None:
    """Kill orphan claude processes from a previous mcloop run.

    When mcloop is killed with kill -9, the claude subprocess
    survives because it runs in its own session. The PID is
    recorded in .mcloop/active-pid so the next run can kill it.

    Before killing, verifies the stored command line matches the
    live process via ``ps -p <pid> -o command=`` to avoid killing
    an unrelated process that reused the same PID.
    """
    pid_file = project_dir / ".mcloop" / "active-pid"
    if not pid_file.exists():
        return
    stored_cmd: str | None = None
    try:
        content = pid_file.read_text().strip()
        try:
            data = _json.loads(content)
            pid = int(data["pid"])
            pgid = int(data.get("pgid", pid))
            stored_cmd = data.get("cmd")
        except (_json.JSONDecodeError, KeyError, TypeError):
            # Fallback: legacy "pid pgid" format
            parts = content.split()
            pid = int(parts[0])
            pgid = int(parts[1]) if len(parts) > 1 else pid
    except (OSError, ValueError, IndexError):
        pid_file.unlink(missing_ok=True)
        return
    # Check if the process is still alive
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        # Already dead
        pid_file.unlink(missing_ok=True)
        return
    except PermissionError:
        pass  # alive but we can't signal it
    # Only kill when verification positively confirms the process matches.
    # Without stored metadata we cannot verify, so treat as stale.
    if stored_cmd is None:
        print(
            formatting.system_msg(f"Stale PID file removed (pid={pid}, no verification metadata)"),
            flush=True,
        )
        pid_file.unlink(missing_ok=True)
        return
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=5,
        )
        live_cmd = result.stdout.strip()
        if not live_cmd:
            # ps returned nothing — cannot positively confirm, treat as stale
            print(
                formatting.system_msg(
                    f"Stale PID file removed (pid={pid}, ps returned no output)"
                ),
                flush=True,
            )
            pid_file.unlink(missing_ok=True)
            return
        if stored_cmd not in live_cmd and live_cmd not in stored_cmd:
            # PID was reused by a different process — do not kill
            print(
                formatting.system_msg(
                    f"Stale PID file removed (pid={pid} is now '{live_cmd}', was '{stored_cmd}')"
                ),
                flush=True,
            )
            pid_file.unlink(missing_ok=True)
            return
    except (OSError, subprocess.TimeoutExpired):
        # Can't verify — remove stale pid file rather than risk killing
        # an unrelated process.
        print(
            formatting.system_msg(f"Stale PID file removed (pid={pid}, could not verify with ps)"),
            flush=True,
        )
        pid_file.unlink(missing_ok=True)
        return
    # Verification positively confirmed — kill the entire process group
    print(
        formatting.error_msg(f"Killing orphan claude process (pid={pid}) from previous run"),
        flush=True,
    )
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            os.kill(pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
    pid_file.unlink(missing_ok=True)


def register_signal_handlers(
    process_ref: object,
    cleanup_callback: object = None,
) -> None:
    """Install signal handlers for graceful shutdown.

    Args:
        process_ref: The runner module (must have ``_interrupted`` and
            ``_active_process`` attributes).
        cleanup_callback: Optional callable invoked before killing the
            active process (e.g. to terminate reviewer subprocesses).
    """

    def _handle_signal(sig, frame):
        process_ref._interrupted = True  # type: ignore[attr-defined]
        _signal_orchestra_interrupt()
        print("\nInterrupted. Saving state...", flush=True)
        _save_interrupt_state()
        if cleanup_callback is not None:
            cleanup_callback()  # type: ignore[operator]
        _graceful_kill_active_process()
        _unlink_active_pid_file()
        print("State saved. Exiting.", flush=True)
        os._exit(130)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTSTP, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGHUP, _handle_signal)


def _signal_orchestra_interrupt() -> None:
    """Flag any orchestra-launched session as interrupted.

    When the orchestra backend is in flight, the active CLI child
    lives in ``orchestra.adapters._subprocess.SessionState``, not in
    ``mcloop.runner._active_process``. Mcloop's signal handler must
    reach both paths so direct-followed-by-orchestra and
    orchestra-followed-by-direct flows both terminate cleanly. Uses
    orchestra's public signal API only (no private attribute access).
    The call is a no-op when no orchestra session is registered, so
    it is safe to invoke unconditionally on every signal.
    """
    if _orchestra_set_interrupted is None:
        return
    try:
        _orchestra_set_interrupted(True)
    except Exception:
        pass


def _kill_orchestra_active_process() -> None:
    """Send SIGTERM (then SIGKILL) to the orchestra session's child group.

    Mirrors the runner-side ``_graceful_kill_active_process`` for the
    orchestra-owned child. Looked up through the public signal API so
    a future orchestra refactor that changes ``SessionState`` does not
    break mcloop.
    """
    accessors = _orchestra_process_accessors(allow_import=True)
    if accessors is None:
        return
    get_active_process, clear_active_process = accessors
    try:
        proc = get_active_process()
    except Exception:
        return
    if proc is None:
        return
    try:
        pgid = os.getpgid(proc.pid)
    except (OSError, ProcessLookupError):
        pgid = proc.pid
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        try:
            proc.terminate()
        except OSError:
            pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            try:
                proc.kill()
            except OSError:
                pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
    try:
        clear_active_process()
    except Exception:
        pass


def _orchestra_process_accessors(
    *,
    allow_import: bool,
) -> tuple[Callable[[], Any], Callable[[], None]] | None:
    """Return orchestra active-process accessors without unsafe late imports."""
    _ = allow_import
    if _orchestra_get_active_process is None or _orchestra_clear_active_process is None:
        return None
    return _orchestra_get_active_process, _orchestra_clear_active_process


def _is_known_interpreter_teardown_error(exc: RuntimeError) -> bool:
    message = str(exc)
    return (
        "can't register atexit after shutdown" in message
        or "interpreter shutdown" in message
        or "sys.meta_path is None" in message
    )


def _kill_active_process(
    *,
    allow_orchestra_import: bool = True,
    propagate_orchestra_errors: bool = False,
) -> None:
    """Kill any active claude subprocess and its process group with SIGKILL.

    Used by the atexit handler where graceful shutdown is not possible.
    Reaches the orchestra session's child as well so the atexit path
    cleans up after orchestra-backed runs too.
    """
    import mcloop.runner as _runner

    proc = _runner._active_process
    if proc is not None:
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            try:
                proc.kill()
            except OSError:
                pass
        _runner._active_process = None
    accessors = _orchestra_process_accessors(allow_import=allow_orchestra_import)
    if accessors is None:
        return
    get_active_process, clear_active_process = accessors
    try:
        oproc = get_active_process()
    except RuntimeError as exc:
        if propagate_orchestra_errors or not _is_known_interpreter_teardown_error(exc):
            raise
        return
    except Exception:
        if propagate_orchestra_errors:
            raise
        return
    if oproc is None:
        return
    try:
        pgid = os.getpgid(oproc.pid)
        os.killpg(pgid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            oproc.kill()
        except OSError:
            pass
    try:
        clear_active_process()
    except RuntimeError as exc:
        if propagate_orchestra_errors or not _is_known_interpreter_teardown_error(exc):
            raise
    except Exception:
        if propagate_orchestra_errors:
            raise


def _graceful_kill_active_process() -> None:
    """Send SIGTERM to the child process group, escalate to SIGKILL after 2s.

    Called by the signal handler. Sends SIGTERM first to give the child
    process group a chance to clean up. If the group does not exit within
    2 seconds, escalates to SIGKILL. Also kills the orchestra-side
    session child if one is registered, so mixed direct/orchestra flows
    do not leak processes when the user interrupts.
    """
    import mcloop.runner as _runner

    _kill_orchestra_active_process()
    proc = _runner._active_process
    if proc is None:
        return
    try:
        pgid = os.getpgid(proc.pid)
    except (OSError, ProcessLookupError):
        pgid = proc.pid
    # Send SIGTERM to the entire process group
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        try:
            proc.terminate()
        except OSError:
            pass
    # Wait up to 2 seconds for graceful exit
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        # Escalate to SIGKILL
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            try:
                proc.kill()
            except OSError:
                pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
    _runner._active_process = None
