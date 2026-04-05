# mcloop:wrap:begin
import hashlib as _mcloop_hashlib
import json as _mcloop_json
import logging as _mcloop_logging
import signal as _mcloop_signal
import sys as _mcloop_sys
import traceback as _mcloop_traceback
from datetime import datetime as _mcloop_datetime, timezone as _mcloop_tz
from pathlib import Path as _mcloop_Path


class _McloopState:
    _providers = []
    _last_action = ""

    @classmethod
    def register(cls, provider):
        cls._providers.append(provider)

    @classmethod
    def record_action(cls, action):
        cls._last_action = str(action)

    @classmethod
    def snapshot(cls):
        result = {}
        for provider in cls._providers:
            try:
                result.update(provider())
            except Exception:
                pass
        return result

    @classmethod
    def last_action(cls):
        return cls._last_action


def _mcloop_setup_crash_handlers():
    error_dir = _mcloop_Path(".mcloop")
    error_dir.mkdir(parents=True, exist_ok=True)
    error_path = error_dir / "errors.json"

    def _write_error(report):
        entries = []
        if error_path.exists():
            try:
                entries = _mcloop_json.loads(error_path.read_text())
            except (ValueError, OSError):
                pass
        trace = report.get("stack_trace", "")
        sig = report.get("signal", report.get("exception_type", ""))
        raw = f"{trace}{sig}".encode()
        report["id"] = _mcloop_hashlib.md5(raw).hexdigest()[:8]
        entries.append(report)
        try:
            error_path.write_text(_mcloop_json.dumps(entries, indent=2) + "\n")
        except OSError:
            pass

    def _excepthook(exc_type, exc_value, exc_tb):
        frames = _mcloop_traceback.extract_tb(exc_tb)
        last = frames[-1] if frames else None
        local_vars = {}
        if exc_tb is not None:
            tb = exc_tb
            while tb.tb_next:
                tb = tb.tb_next
            local_vars = {
                k: repr(v) for k, v in tb.tb_frame.f_locals.items() if not k.startswith("_")
            }
        state = _McloopState.snapshot()
        state.update(local_vars)
        report = {
            "timestamp": _mcloop_datetime.now(_mcloop_tz.utc).isoformat(),
            "exception_type": exc_type.__name__,
            "description": str(exc_value),
            "stack_trace": "".join(
                _mcloop_traceback.format_exception(exc_type, exc_value, exc_tb)
            ),
            "source_file": last.filename if last else "",
            "line": last.lineno if last else 0,
            "app_state": state,
            "last_action": _McloopState.last_action(),
            "fix_attempts": 0,
        }
        _write_error(report)
        _loc = f"{last.filename}:{last.lineno}" if last else "unknown"
        _mcloop_sys.stderr.write(
            f"[McLoop] Crash captured: {exc_type.__name__} in {_loc}."
            f" Run mcloop from /Users/mhcoen/proj/duplo"
            f" to fix this bug.\n"
        )
        _mcloop_sys.__excepthook__(exc_type, exc_value, exc_tb)

    _mcloop_sys.excepthook = _excepthook

    def _signal_handler(signum, frame):
        source = ""
        lineno = 0
        if frame is not None:
            source = frame.f_code.co_filename
            lineno = frame.f_lineno
        # Avoid calling provider closures in signal context
        # (they may hold locks or do I/O). Read raw state only.
        try:
            state = dict(_McloopState.snapshot())
        except Exception:
            state = {}
        report = {
            "timestamp": _mcloop_datetime.now(_mcloop_tz.utc).isoformat(),
            "signal": signum,
            "exception_type": "Signal",
            "description": f"Received signal {signum}",
            "stack_trace": "".join(_mcloop_traceback.format_stack(frame)),
            "source_file": source,
            "line": lineno,
            "app_state": state,
            "last_action": _McloopState.last_action(),
            "fix_attempts": 0,
        }
        try:
            _write_error(report)
        except Exception:
            pass
        _loc = f"{source}:{lineno}" if source else "unknown"
        try:
            _mcloop_sys.stderr.write(
                f"[McLoop] Crash captured: Signal {signum} in {_loc}."
                f" Run mcloop from /Users/mhcoen/proj/duplo"
                f" to fix this bug.\n"
            )
        except Exception:
            pass
        _mcloop_signal.signal(signum, _mcloop_signal.SIG_DFL)
        import os

        os.kill(os.getpid(), signum)

    for _sig in (
        _mcloop_signal.SIGSEGV,
        _mcloop_signal.SIGABRT,
    ):
        try:
            _mcloop_signal.signal(_sig, _signal_handler)
        except OSError:
            pass

    class _McloopLogHandler(_mcloop_logging.Handler):
        def emit(self, record):
            if record.exc_info and record.exc_info[1] is not None:
                exc_type, exc_value, exc_tb = record.exc_info
                frames = _mcloop_traceback.extract_tb(exc_tb)
                last = frames[-1] if frames else None
                local_vars = {}
                if exc_tb is not None:
                    tb = exc_tb
                    while tb.tb_next:
                        tb = tb.tb_next
                    local_vars = {
                        k: repr(v)
                        for k, v in tb.tb_frame.f_locals.items()
                        if not k.startswith("_")
                    }
                state = _McloopState.snapshot()
                state.update(local_vars)
                report = {
                    "timestamp": _mcloop_datetime.now(_mcloop_tz.utc).isoformat(),
                    "exception_type": exc_type.__name__,
                    "description": str(exc_value),
                    "stack_trace": "".join(
                        _mcloop_traceback.format_exception(exc_type, exc_value, exc_tb)
                    ),
                    "source_file": last.filename if last else "",
                    "line": last.lineno if last else 0,
                    "app_state": state,
                    "last_action": _McloopState.last_action(),
                    "fix_attempts": 0,
                }
                _write_error(report)

    handler = _McloopLogHandler()
    handler.setLevel(_mcloop_logging.ERROR)
    _mcloop_logging.getLogger().addHandler(handler)


_mcloop_setup_crash_handlers()
# mcloop:wrap:end
