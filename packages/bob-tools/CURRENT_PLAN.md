## Stage 4: Renderer

- [x] [BATCH] Implement `render_plan(plan: Plan) -> str`
   - [x] Render order: magic line (if present in the input or required by strict output mode), blank line, project H1, blank line, preamble (if any), blank line, each phase in order, then bugs section if present.
   - [x] Phase rendering: heading line `## {keyword} {ordinal}: {title}`, then on the next line the phase-id comment if `phase_id_source != "none"`, then blank line, then prose (if any), then blank line, then subsections in order, then tasks in order.
   - [x] Canonical phase-id position is always the comment form, even when input used the legacy header form. The renderer is what migrates legacy header to comment per design doc section 7.1.
   - [x] Task rendering: `{indent}- [{status_char}] {task_id_prefix}{flag_tag_block}{action_tag_block}{text} {annotations}`. Status char: TODO renders as space, DONE as x, FAILED as exclamation mark. Flag tags ordered by source position; action tag immediately after flag tags. Annotations at end of line, separated by spaces.
   - [x] When `task_id` is None (compat-mode plan being rendered without identity migration), omit the task-id prefix. This is the same plan a compat-mode parse produced, rendered back unchanged.
   - [x] @deps line rendering: when a task has non-empty `deps`, render `{child_indent}@deps {id} {id} ...` on the line immediately after the task line.
   - [x] Subsection rendering: blank line, sub-heading, blank line, prose (if any), blank line, tasks in order.
   - [x] Bugs section rendering: blank line, the Bugs H2 heading, blank line, tasks in order.
   - [x] Indentation: 2 spaces per nesting level. Canonical, per design doc section 4.2 Notes.
   - [x] Trailing newline at end of file. Always exactly one.
   - [x] Tests: render output matches a hand-written fixture byte-for-byte for a small Plan; output ends with exactly one newline; indentation always 2 spaces regardless of input indentation.

- [x] Round-trip property tests
   - [x] Implement two property tests in `tests/test_roundtrip.py`:
   - [x] `test_parse_render_parse_idempotent`: for each fixture text, `parse(render(parse(text)))` equals `parse(text)` on the Plan value (ignoring line_number fields which differ between iterations). Fixtures are hand-crafted small plans covering each tag, each heading form, each status, the bugs section, subsections, RULEDOUT lines, and @deps lines.
   - [x] `test_render_parse_render_stable`: for each fixture plan, `render(parse(render(plan)))` equals `render(plan)`. This is the canonical-form fixed-point property.
   - [x] Fixtures live in `bob_tools/planfile/tests/fixtures/` as markdown files; the test loads them at runtime.

- [x] Generative property tests
   - [x] Add `tests/test_generative.py`. Implement a small Plan generator using stdlib (no Hypothesis dependency): random small valid trees with random phase counts, random task counts per phase, random tag combinations, random deps among declared IDs. Per Codex's pile-5 acceptance test gap.
   - [x] Properties: `parse(render(plan))` equals `plan` modulo line numbers; task IDs in the rendered plan are unique; `next_tasks` returns tasks in the expected canonical order (defer this property to after Stage 5 lands `next_tasks`).
   - [x] Run 100 random plans per property by default; bump to 1000 in a slow-mode pytest marker.

- [ ] Canonicalization function
   - [x] Implement `canonicalize(text: str) -> str` as `render_plan(parse_plan(text))`. Lossless formatting only. Does not assign IDs or add phase-id comments — that is the `migrate` operation in Stage 5. Per design doc section 3.2.
   - [ ] Test: `canonicalize(canonicalize(text))` equals `canonicalize(text)` for every fixture. Test: tasks without IDs in the input have no IDs in the output (canonicalize does not migrate).

- [ ] Verify Stage 4 leaves the repo green.
