# Bugs

## bob_tools/planfile/operations.py:622-630 -- `[BATCH]` parent surfaces with empty unit when first actionable child is `[USER]` or `[AUTO:...]`
**Severity**: medium

`_walk_actionable` decides to yield a surfaced `[BATCH]` parent based on
whether the inner recursion yields anything at all (`first_child is not
None`), but the surfaced parent's children are computed independently by
`_get_batch_children`, which stops at the first `[USER]`/`[AUTO:...]`
child. When the first non-DONE child of a `[BATCH]` parent is `[USER]`
or `[AUTO:...]`, the recursion still yields that child (the inner walker
ignores the tag) so `first_child is not None`, but `_get_batch_children`
returns `()`. The code yields `_surface_batch_parent(task)` — a copy of
the parent with `children=()` — and then drains the iterator (lines
629-630) so the actionable `[USER]`/`[AUTO]` child and any later siblings
are discarded.

Concretely, for a plan fragment like:

```
- [ ] T-001: [BATCH] parent
  - [ ] T-002: [USER] verify thing
  - [ ] T-003: do other thing
```

`next_tasks(plan, limit=1)` returns `[T-001]` with `children=()`. T-002
(actionable, USER) and T-003 (actionable) are not surfaced. Re-calling
`next_tasks` produces the same empty BATCH parent, so the workflow
stalls: there is nothing to act on in the returned task, and the only
way forward is to mark T-001 done out-of-band (which itself violates the
parent-derived-from-children rule). The fix is to guard the BATCH branch
on a non-empty batch (e.g., compute `_get_batch_children(task)` once and
fall through to the non-BATCH yield path when it is empty).
