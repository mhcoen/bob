"""Interactive REPL for the verb-style CLI.

Bare ``orchestra`` (no arguments) drops into this loop. The user
types a verb-and-question line, sees the model's answer, and can
follow up with another line that references prior turns. The REPL
also handles slash commands for switching verbs, viewing the
transcript, and saving it.

Design choices:

- ``prompt_toolkit.PromptSession`` for the input primitive. Gives
  history file, auto-suggest from history, and clean Ctrl-D handling
  for free.
- Slash commands (lines starting with ``/``) dispatch through a
  small in-process table. Anything else is treated as a query and
  routed to ``run_verb`` with the active verb plus the in-memory
  transcript as ``history``.
- Session context is in-memory only. The user opts in to disk via
  ``/save``. The history file at ``~/.orchestra/history`` is
  prompt_toolkit's recall mechanism for past commands; it is not the
  conversational transcript.
- Ctrl-D exits cleanly. A first Ctrl-C cancels the current input
  line; a second within ``_DOUBLE_CTRL_C_WINDOW`` exits the loop.
- Verb invocation errors print to stderr but the REPL stays alive
  so a stray failure does not eject the user.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory

from orchestra.adapters._subprocess import get_current_activity
from orchestra.api import run_verb
from orchestra.config import OrchestraConfig
from orchestra.errors import OrchestraError
from orchestra.progress import ProgressCallback, stderr_reporter

_HISTORY_PATH: Path = Path.home() / ".orchestra" / "history"
_DOUBLE_CTRL_C_WINDOW: float = 1.0


@dataclass
class Turn:
    """One question/answer pair plus the verb that produced it."""

    verb: str
    query: str
    answer: str


@dataclass
class ReplState:
    """In-memory session state. Reset on /clear, lost on exit."""

    config: OrchestraConfig
    current_verb: str
    turns: list[Turn] = field(default_factory=list)
    progress_callback: ProgressCallback | None = None
    # Threaded into run_verb so a project-local
    # ``.orchestra/workflows/<name>.orc`` overrides the packaged
    # workflow when the REPL is launched from a project directory.
    project_dir: Path | None = None


def _default_verb(config: OrchestraConfig) -> str | None:
    """Return the default verb for a session.

    Honors a top-level ``default_verb`` key in the config when
    present and configured. Otherwise picks the first verb in the
    table (sorted alphabetically for stability across reloads).
    Returns ``None`` only when no verbs are configured.
    """
    explicit = getattr(config, "default_verb", None)
    if isinstance(explicit, str) and explicit in config.verbs:
        return explicit
    if not config.verbs:
        return None
    return sorted(config.verbs)[0]


def format_history(turns: list[Turn]) -> str:
    """Format prior turns as a transcript prefix for the next prompt.

    Returns the empty string when ``turns`` is empty so a template
    that inlines ``{history}{query}`` produces no orphaned header on
    the first turn. Otherwise returns
    ``"Prior conversation:\\n<lines>\\n\\n"`` where each line is one
    user/assistant pair.
    """
    if not turns:
        return ""
    lines: list[str] = []
    for turn in turns:
        lines.append(f"user: {turn.query}")
        lines.append(f"assistant: {turn.answer}")
    return "Prior conversation:\n" + "\n".join(lines) + "\n\n"


def _format_markdown_transcript(turns: list[Turn]) -> str:
    """Render the transcript as a human-friendly markdown document."""
    if not turns:
        return "# Orchestra session\n\n_(no turns)_\n"
    parts: list[str] = ["# Orchestra session", ""]
    for i, turn in enumerate(turns, 1):
        parts.append(f"## Turn {i} ({turn.verb})")
        parts.append("")
        parts.append("**You:**")
        parts.append("")
        parts.append(turn.query)
        parts.append("")
        parts.append("**Assistant:**")
        parts.append("")
        parts.append(turn.answer)
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _format_json_transcript(turns: list[Turn]) -> str:
    return (
        json.dumps(
            [{"verb": t.verb, "query": t.query, "answer": t.answer} for t in turns],
            indent=2,
        )
        + "\n"
    )


# --------------------------------------------------------------------
# Slash-command dispatcher
# --------------------------------------------------------------------


@dataclass
class SlashOutcome:
    """Return shape from a slash command. ``exit`` ends the REPL."""

    exit: bool = False


SlashCommand = Callable[[ReplState, list[str]], SlashOutcome]


def _cmd_help(state: ReplState, _args: list[str]) -> SlashOutcome:
    print("Slash commands:")
    print("  /help          show this list")
    print("  /use [name]    show or switch the active workflow")
    print("  /clear         clear the in-memory transcript")
    print("  /history       print the transcript")
    print("  /save <path>   write the transcript to a file")
    print("  /exit, /quit   leave the REPL")
    print("Configured workflows:")
    if not state.config.verbs:
        print("  (none)")
    else:
        for name in sorted(state.config.verbs):
            print(f"  {name}  runs {state.config.verbs[name].workflow}")
    return SlashOutcome()


def _cmd_use(state: ReplState, args: list[str]) -> SlashOutcome:
    if not args:
        print(f"current workflow: {state.current_verb}")
        return SlashOutcome()
    target = args[0]
    if target not in state.config.verbs:
        print(
            f"unknown workflow {target!r}. Configured: {sorted(state.config.verbs)}",
            file=sys.stderr,
        )
        return SlashOutcome()
    state.current_verb = target
    print(f"workflow -> {target}")
    return SlashOutcome()


def _cmd_clear(state: ReplState, _args: list[str]) -> SlashOutcome:
    n = len(state.turns)
    state.turns.clear()
    print(f"cleared {n} turn(s).")
    return SlashOutcome()


def _cmd_history(state: ReplState, _args: list[str]) -> SlashOutcome:
    if not state.turns:
        print("(no turns yet)")
        return SlashOutcome()
    for i, turn in enumerate(state.turns, 1):
        print(f"--- Turn {i} ({turn.verb}) ---")
        print(f"you: {turn.query}")
        print(f"assistant: {turn.answer}")
    return SlashOutcome()


def _cmd_save(state: ReplState, args: list[str]) -> SlashOutcome:
    if not args:
        print("usage: /save <path>", file=sys.stderr)
        return SlashOutcome()
    path = Path(args[0]).expanduser()
    suffix = path.suffix.lower()
    if suffix == ".json":
        body = _format_json_transcript(state.turns)
    else:
        body = _format_markdown_transcript(state.turns)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    except OSError as exc:
        print(f"could not write {path}: {exc}", file=sys.stderr)
        return SlashOutcome()
    print(f"saved {len(state.turns)} turn(s) to {path}")
    return SlashOutcome()


def _cmd_exit(_state: ReplState, _args: list[str]) -> SlashOutcome:
    return SlashOutcome(exit=True)


_SLASH_COMMANDS: dict[str, SlashCommand] = {
    "/help": _cmd_help,
    "/use": _cmd_use,
    "/clear": _cmd_clear,
    "/history": _cmd_history,
    "/save": _cmd_save,
    "/exit": _cmd_exit,
    "/quit": _cmd_exit,
}


def dispatch_slash(state: ReplState, line: str) -> SlashOutcome:
    """Run a slash-command line. Unknown commands print a hint."""
    parts = line.strip().split()
    if not parts:
        return SlashOutcome()
    name = parts[0]
    args = parts[1:]
    handler = _SLASH_COMMANDS.get(name)
    if handler is None:
        print(
            f"unknown slash command {name!r}. Try /help.",
            file=sys.stderr,
        )
        return SlashOutcome()
    return handler(state, args)


# --------------------------------------------------------------------
# Verb invocation from the REPL
# --------------------------------------------------------------------


def handle_query(state: ReplState, line: str) -> None:
    """Run a query line against a verb, append a turn on success.

    If the line's first word matches a configured verb name, that
    word is the per-turn verb dispatcher and the rest of the line
    is the query. The user's typed verb word is dispatcher metadata,
    not content the model should see, so it is peeled off before
    the prompt is built. The session's persistent ``current_verb``
    is unchanged. The next bare-line entry still routes through
    ``current_verb``; the user persistently switches via ``/use``.

    If the first word does not match a configured verb, the line
    is treated as a query under ``current_verb`` (the legacy path).

    Errors print to stderr but do not exit the REPL.
    """
    parts = line.strip().split(None, 1)
    first = parts[0] if parts else ""
    if first in state.config.verbs:
        verb = first
        query = parts[1] if len(parts) > 1 else ""
        if not query:
            print(f"usage: {verb} <query>", file=sys.stderr)
            return
    else:
        verb = state.current_verb
        query = line
    history = format_history(state.turns)
    try:
        answer = run_verb(
            verb,
            query,
            state.config,
            history=history,
            progress_callback=state.progress_callback,
            project_dir=state.project_dir,
        )
    except OrchestraError as exc:
        print(str(exc), file=sys.stderr)
        return
    print(answer)
    state.turns.append(Turn(verb=verb, query=query, answer=answer))


# --------------------------------------------------------------------
# Top-level loop
# --------------------------------------------------------------------


def _build_session() -> PromptSession[str]:
    # Pass-8 fix #3: REPL history persists raw user queries. The
    # default umask (022) creates ~/.orchestra at 0755 and the
    # history file at 0644, so other local users on a shared host
    # can read the queries the audit verified can carry secrets.
    # Tighten the parent directory to 0700 and the history file to
    # 0600 before prompt_toolkit opens it. History stays enabled so
    # up-arrow recall keeps working; the residual (root or backup
    # processes) matches every shell history file's residual.
    parent = _HISTORY_PATH.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        parent.chmod(0o700)
    except OSError:
        pass
    if not _HISTORY_PATH.exists():
        _HISTORY_PATH.touch(mode=0o600)
    try:
        _HISTORY_PATH.chmod(0o600)
    except OSError:
        pass
    return PromptSession(
        history=FileHistory(str(_HISTORY_PATH)),
        auto_suggest=AutoSuggestFromHistory(),
    )


def _prompt_string(state: ReplState, default_verb: str | None) -> str:
    if default_verb is not None and state.current_verb == default_verb:
        return "orchestra> "
    return f"orchestra ({state.current_verb})> "


def run_repl(
    config: OrchestraConfig,
    *,
    session: Any | None = None,
    progress_callback: ProgressCallback | None = None,
    project_dir: Path | None = None,
) -> int:
    """Run the interactive REPL. Returns the process exit code.

    ``progress_callback`` defaults to a stderr reporter so the user
    sees per-state progress while a multi-role verb runs. Pass
    ``progress_callback=None`` (or invoke the CLI with ``--quiet``)
    to suppress.

    ``project_dir`` defaults to the current working directory so a
    project-local override at ``<cwd>/.orchestra/workflows/<name>.orc``
    is honoured by every verb run from this REPL session.
    """
    default = _default_verb(config)
    if default is None:
        print(
            "no verbs configured; cannot start REPL. "
            "Add a verb mapping to ~/.orchestra/config.json. "
            "See `orchestra help` for the format.",
            file=sys.stderr,
        )
        return 1
    if progress_callback is None:
        progress_callback = stderr_reporter(activity_getter=get_current_activity)
    if project_dir is None:
        project_dir = Path.cwd()
    state = ReplState(
        config=config,
        current_verb=default,
        progress_callback=progress_callback,
        project_dir=project_dir,
    )
    if session is None:
        session = _build_session()

    completer = WordCompleter(
        sorted(config.verbs) + sorted(_SLASH_COMMANDS),
        ignore_case=True,
    )

    print("orchestra REPL. /help for commands, /exit to quit.")
    last_ctrl_c = 0.0
    while True:
        try:
            line = session.prompt(
                _prompt_string(state, default),
                completer=completer,
            )
        except KeyboardInterrupt:
            now = time.monotonic()
            if now - last_ctrl_c < _DOUBLE_CTRL_C_WINDOW:
                print("(double Ctrl-C, exiting)")
                return 0
            last_ctrl_c = now
            print("(press Ctrl-C again to exit, or Ctrl-D)")
            continue
        except EOFError:
            print()
            return 0
        last_ctrl_c = 0.0
        if not line.strip():
            continue
        if line.startswith("/"):
            outcome = dispatch_slash(state, line)
            if outcome.exit:
                return 0
            continue
        handle_query(state, line)
