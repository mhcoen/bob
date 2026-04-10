# Ideas

A flat scratchpad for ideas not yet ready to become PLAN.md tasks.
Add anything here — feature sketches, half-baked thoughts, things to
explore later. McLoop does not read or modify this file during runs.
Use `mcloop idea "some text"` to append from the command line.

- 2026-04-10: Branch/worktree isolation for normal mcloop runs. Investigation mode already does this. The main run_loop still mutates the live working tree, which means a mid-run crash, kill, or botched rollback can leave the real working directory dirty. Per-run worktrees would shrink the blast radius, enable parallel runs against the same project, and make interrupted-run recovery cleaner. Open design questions: venv handling per worktree (share parent, recreate, or use a faster dependency cache like uv), auto-wrap interaction (the wrap markers live in source files and would be lost if a worktree is discarded), and whether this should be opt-in via `--worktree` or eventually become the default. Likely opt-in first for high-risk runs (overnight, bug-only mode, long stages) and leave quick interactive runs in-place.
