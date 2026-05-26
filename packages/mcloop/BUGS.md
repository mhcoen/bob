## Bugs

- [ ] Live-activity surfacing missing in orchestra-routed agent sessions.
  T-000095 (Stage 1) implemented status-line surfacing for mcloop's
  direct `claude -p` subprocess wrapping, but the orchestra-routed
  `claude_code_agent` code path emits only the minimal ticker
  (`[1/1] editor (claude_code_agent:opus) ... still running, Xs elapsed`)
  with no indication of what the agent is currently doing. The active
  session's stream-json log contains every tool_use and tool_result
  event; the fix is for whichever component prints the 30s ticker to
  tail that log and surface the most recent tool_use as a second line
  beneath the elapsed-time line. Two-line format required: the
  existing elapsed-time line is already at the terminal-width ceiling
  on common laptop setups, so the activity summary must not be
  appended to it. Example:

      [1/1] editor (claude_code_agent:opus) ... still running, 360.0s elapsed
          running: Read /Users/mhcoen/proj/bob/packages/bob-tools/PLAN.md

  Indent the activity line so it visually attaches to the ticker
  without competing with it. Truncate the activity content with an
  ellipsis if it would still exceed terminal width
  (`shutil.get_terminal_size().columns`) — do not wrap. Confirm
  whether the ticker is emitted by mcloop, orchestra's executor, or
  the claude_code_agent adapter, and fix at the lowest layer that
  has the active log path. Cost is trivial: read last few KB of the
  log, find most recent tool_use block, format.
