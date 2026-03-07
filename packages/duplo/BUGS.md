# Bugs

## claude_cli.py:30 -- No subprocess timeout on claude CLI calls
**Severity**: medium
Both `query()` (line 30) and `query_with_images()` (line 74) call `subprocess.run()` without a `timeout` parameter. If the `claude -p` process hangs (network issue, authentication prompt, resource exhaustion), the entire duplo process blocks indefinitely with no way to recover. Every other subprocess call in the codebase that could hang (ffmpeg in `video_extractor.py:125`, mcloop in `runner.py:23`) has a timeout or streams output, but the claude CLI calls do not.

## main.py:748 -- Phase completion detection uses fragile string matching
**Severity**: medium
The history check at line 748 compares `phase_label` (constructed from roadmap data as `f"Phase {phase_num}: {phase_info['title']}"`) against history entries (which store the heading extracted from PLAN.md via regex). If Claude generates a PLAN.md heading that differs from the roadmap title (e.g., roadmap says "Core" but Claude generates "# Phase 1: Core Features"), the equality and `startswith` checks both fail. This causes the code to not recognize the phase as completed. If duplo is restarted after a crash between `append_phase_to_history` (line 1036) and `advance_phase` (line 1037), the completed phase would be re-planned and re-executed instead of being detected as already done.

## video_extractor.py:194 -- Type annotation mismatch allows None in int-typed list
**Severity**: low
`kept` is declared as `list[tuple[Path, int]]` at line 194, but `(frame, None)` is appended at line 202 when hashing fails. The code works at runtime because line 208 guards with `kh is not None`, but the type annotation is incorrect (should be `list[tuple[Path, int | None]]`). Additionally, lines 205-207 (`if h is None: kept.append((frame, None))`) are dead code: `_dhash()` always returns an `int`, and any exception during `Image.open()` or `_dhash()` is caught by the except block at line 199. The `h is None` condition on line 205 is unreachable.
