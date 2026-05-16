# Bugs

## bob_tools/planfile/renderer.py:182 -- Renderer-parser round-trip drops task_id when task has empty text

**Severity**: low
For a `Task` whose `task_id` is set but `text`, `flag_tags`, `action_tag`, and `annotations` are all empty, `_render_task_lines` builds `body_parts = ["T-NNNNNN:"]`, joins to `body = "T-NNNNNN:"`, and produces the line `"- [ ] T-NNNNNN:"` (the trailing space from the `" {body}"` template is removed by `.rstrip()` on line 182).

Re-parsing that line, `_CHECKBOX_RE` matches with text `"T-NNNNNN:"`, but `_extract_task_id` (parser.py:633) uses `_TASK_ID_RE = re.compile(r"^T-(\d+):\s+(.*)$")` which requires *at least one* whitespace character after the colon. With nothing after the colon, the match fails, so `_extract_task_id` returns `(None, "T-NNNNNN:")`. The parsed `Task` therefore has `task_id=None` and `text="T-NNNNNN:"` — the stable id is silently lost and embedded into the text body.

This is reachable via the public `add_task(plan, phase_id, text="")` API (operations.py:1069), which creates exactly this shape (auto-assigned `task_id`, empty `text`, no other fields). Following `save(path, plan_after_add)` with a subsequent `load(path)` returns a plan in which the new task's `task_id` is gone and `@deps` references / `complete_task` lookups against that id will no longer resolve.
