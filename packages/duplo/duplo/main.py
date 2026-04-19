from __future__ import annotations

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

"""Duplo CLI entry point.

The heavy lifting lives in :mod:`duplo.pipeline` (orchestration) and
:mod:`duplo.status` (display).  This module is only argument parsing,
subcommand dispatch, and signal/crash handler wiring.
"""

import argparse
import os
import signal
import sys
from pathlib import Path

from duplo import pipeline as _pipeline
from duplo.diagnostics import print_summary as diagnostics_print_summary
from duplo.migration import _check_migration
from duplo.screenshotter import save_reference_screenshots  # noqa: F401  # legacy re-export
from duplo.selector import select_features, select_issues  # noqa: F401  # legacy re-export

_DUPLO_JSON = ".duplo/duplo.json"
# Files that are project artifacts, not user-provided reference materials.
_PROJECT_FILES = {"PLAN.md", "CLAUDE.md", "README.md", "ISSUES.md", "NOTES.md", "SPEC.md"}


def main() -> None:
    """Run duplo from the current directory.

    First run (no ``.duplo/duplo.json``): scan for reference materials,
    fetch URLs, extract features, generate roadmap and plan, build.

    Subsequent runs: resume interrupted phases or advance to the next one.
    """
    # Check for subcommands before parsing, since the default
    # mode uses a positional 'url' arg that would eat 'fix'/'investigate'/'init'.
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        from duplo.init import run_init

        init_parser = argparse.ArgumentParser(
            prog="duplo init",
            description="Initialize a new duplo project (writes SPEC.md and ref/).",
        )
        init_parser.add_argument(
            "url",
            nargs="?",
            default=None,
            help="Product URL (must start with http:// or https://).",
        )
        init_parser.add_argument(
            "--from-description",
            dest="from_description",
            default=None,
            metavar="PATH",
            help="Path to a prose description file, or - for stdin.",
        )
        init_parser.add_argument(
            "--deep",
            action="store_true",
            default=False,
            help="Opt in to deep scraping during init.",
        )
        init_parser.add_argument(
            "--force",
            action="store_true",
            default=False,
            help="Overwrite an existing SPEC.md.",
        )
        init_args = init_parser.parse_args(sys.argv[2:])
        init_args.command = "init"
        # Defer URL validation to run_init when --from-description is
        # also set, so run_init can stack errors (invalid URL AND
        # missing description file) per INIT-design.md § "Both init
        # arguments invalid".
        if (
            init_args.url is not None
            and init_args.from_description is None
            and not init_args.url.startswith(("http://", "https://"))
        ):
            print(
                f"Error: {init_args.url!r} is not a valid URL.\n"
                "  URLs must start with http:// or https://.\n"
                "  To set up without a URL, run `duplo init` (no arguments)."
            )
            sys.exit(2)
        run_init(init_args)
        diagnostics_print_summary()
        return

    if len(sys.argv) > 1 and sys.argv[1] in ("fix", "investigate"):
        subcmd = sys.argv[1]
        fix_parser = argparse.ArgumentParser(
            prog=f"duplo {subcmd}",
            description=(
                "Investigate bugs with product-level AI diagnosis."
                if subcmd == "investigate"
                else "Report bugs and append fix tasks to the current PLAN.md."
            ),
        )
        fix_parser.add_argument(
            "bugs",
            nargs="*",
            help="Bug descriptions (one per argument). Use quotes for multi-word.",
        )
        fix_parser.add_argument(
            "--file",
            "-f",
            dest="bug_file",
            default=None,
            help="Read bug descriptions from a file (one per paragraph, blank-line separated).",
        )
        fix_parser.add_argument(
            "--screenshot",
            "-s",
            action="store_true",
            default=False,
            help="Capture a screenshot of the running app for context.",
        )
        fix_parser.add_argument(
            "--investigate",
            "-i",
            action="store_true",
            default=False,
            help=(
                "Alias for bare `duplo fix` (which also runs investigation). "
                "Retained for clarity and for use as `duplo investigate`."
            ),
        )
        fix_parser.add_argument(
            "--images",
            nargs="+",
            default=None,
            metavar="PATH",
            help="User-supplied screenshot files showing the bug.",
        )
        args = fix_parser.parse_args(sys.argv[2:])
        args.command = subcmd
        # 'duplo investigate' implies --investigate.
        if subcmd == "investigate":
            args.investigate = True
    else:
        parser = argparse.ArgumentParser(
            description="Duplicate an app from reference materials or a product URL.",
        )
        parser.add_argument(
            "url",
            nargs="?",
            default=None,
            help="Product URL to duplicate (e.g. https://numi.app)",
        )
        args = parser.parse_args()
        args.command = None

    def _handle_signal(signum, frame):
        print("\nInterrupted.", flush=True)
        os._exit(130)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTSTP, _handle_signal)

    duplo_path = Path(_DUPLO_JSON)

    if args.command in ("fix", "investigate"):
        if not duplo_path.exists():
            print("No duplo project found. Run duplo first to initialize.")
            sys.exit(1)
        _pipeline._fix_mode(args)
    else:
        _check_migration(Path.cwd())
        spec_path = Path.cwd() / "SPEC.md"
        if spec_path.exists():
            if args.url:
                print("Project already initialized. URL argument ignored.")
            _pipeline._subsequent_run()
        elif not duplo_path.exists():
            print("No SPEC.md found. Run `duplo init` first to create SPEC.md.")
            sys.exit(0)
        else:
            # duplo.json exists but SPEC.md does not - migration check
            # should have caught this. Defensive fallback in case the
            # migration signals ever diverge.
            print(
                "No SPEC.md found. Run `duplo init` first to create SPEC.md.",
                file=sys.stderr,
            )
            sys.exit(1)

    diagnostics_print_summary()
