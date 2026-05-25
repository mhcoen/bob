# Slice C kickoff

Workflow at `/Users/mhcoen/proj/orchestra` is at `slice-b-complete`
(`31357d2`). Slice A and Slice B are done. The plan is at
`/Users/mhcoen/proj/orchestra/design/orchestra-real-council-plan.md`.
Section 4 is the workflow spec, Section 5 is the templates spec,
Section 3 is the role naming, Slice C's own implementation list is
at the end. Note: Section 2's type list was updated today to remove
`bytes`; the implementation already reflects this and no Slice C
work depends on `bytes`.

Build Slice C per the plan. One commit on top of `slice-b-complete`.
After the commit pushes, retag `slice-c-complete` to point at it.
Stop and report. Do not begin Slice D.

The slice contents:

1. Workflow file `orchestra/workflows/ask_council.orc` with thirteen
   states per Section 4: frame, five advisor states, anonymize, five
   reviewer states, chairman.
   - `frame`: text-role, role `framer`, reads query and history
     (external_inputs), writes `framed_question`.
   - Five advisor states (`contrarian_advise`,
     `first_principles_advise`, `expansionist_advise`,
     `outsider_advise`, `executor_lens_advise`): each text-role
     with the corresponding lens role binding, reads
     `framed_question`, writes its own `<lens>_output` artifact.
     Spawned via `fan_out` from `frame`, joining at `anonymize`,
     with `on error stop`.
   - `anonymize`: transform state using the registered
     `anonymize_outputs` transform, reads all five advisor outputs,
     writes `anon_map` only.
   - Five reviewer states (`reviewer_1` through `reviewer_5`): each
     text-role with the `reviewer` role binding, reads `anon_map`,
     writes `review_N_output`. Spawned via `fan_out` from
     `anonymize`, joining at `chairman`, with `on error stop`.
   - `chairman`: text-role with the `chairman` role binding, reads
     `framed_question`, all five named advisor outputs
     (`contrarian_output`, `first_principles_output`,
     `expansionist_output`, `outsider_output`,
     `executor_lens_output`), and all five reviewer outputs. Writes
     `chairman_output`. Terminal state.
   - Both fan-out error targets are `stop`. This keeps the workflow
     to the listed states and matches the current validator, which
     allows `done` and `stop` as fan-out error targets. Add a short
     workflow comment documenting that failed council fan-outs stop
     the run rather than trying to synthesize partial output.

2. Eight template files in `orchestra/templates/`:
   - `ask_council_framer.md`: per Section 5, takes query plus
     history, produces a clear neutral framed question.
   - `ask_council_contrarian.md`,
     `ask_council_first_principles.md`,
     `ask_council_expansionist.md`, `ask_council_outsider.md`,
     `ask_council_executor_lens.md`: each contains the lens
     description, the framed question, instructs the model to
     lean fully into its angle, 150-300 words target. Lens
     descriptions per Karpathy's reference (see
     `/Users/mhcoen/.claude/skills/llm-council/SKILL.md`).
   - `ask_council_reviewer.md`: takes `anon_map` A-E, asks the
     three review questions per Section 5, under 200 words.
     Critical: the reviewer prompt MUST NOT contain lens
     identifiers; it sees only A-E. This is a workflow contract,
     not a template suggestion. The same template is used by all
     five reviewer states.
   - `ask_council_chairman.md`: takes framed question, named
     advisor outputs, all five reviews, produces the structured
     verdict with the headers from Section 5: Where Council
     Agrees, Where Council Clashes, Blind Spots Caught,
     Recommendation, One Thing to Do First.

3. Role naming per Section 3. Eight required role bindings for
   `ask_council`:
   - `framer`
   - `contrarian`, `first_principles`, `expansionist`, `outsider`,
     `executor_lens` (the five advisor lens roles)
   - `reviewer`
   - `chairman`

4. Validator update: reject a config that does not bind all eight
   required roles when `ask_council` is invoked. The error message
   must name each missing binding explicitly. Role-binding config
   validation lives in `orchestra/api.py`, not the workflow
   validator. Touch `orchestra/api.py` and related tests only if
   the existing role-binding validation is not sufficient. Reuse
   the existing infrastructure if it is.

5. Verb mapping note: per the plan's Section 7, the user updates
   `~/.orchestra/config.json` themselves. Code does not edit it.
   Do not modify the user's config in this slice.

6. Tests required (full list from Slice C in the plan):
   - Workflow loads and validates against a config that binds all
     eight required roles.
   - End-to-end run with mock adapters: chairman_output prompt
     contains all five named advisor outputs and all five
     reviewer outputs. Use the same mock-adapter pattern Slice A
     and Slice B tests use.
   - Reviewer prompt isolation: assert reviewer prompts contain
     `anon_map` A-E values and DO NOT contain any of the strings
     `contrarian`, `first_principles`, `expansionist`, `outsider`,
     `executor_lens`. This catches anonymization regressions. Test
     must check the actual rendered prompt text passed to the
     adapter, not just the workflow's structural reads.
   - Reviewer statelessness: assert each reviewer invocation
     receives a fresh adapter call with no shared session id or
     continuation token. The text-role adapter's existing
     stateless invocation model should make this assertion
     straightforward.
   - Validator rejects a config missing one of the eight required
     bindings: enumerate all eight cases (each missing in turn),
     assert the error message names the specific missing binding.
   - End-to-end determinism: same `(run_id, query, history)`
     produces the same `anon_map` across two runs (this exercises
     Slice B's seed determinism through the full workflow rather
     than at the transform layer alone).

7. Mock adapters for tests: reuse the patterns established in
   Slice A and Slice B test fixtures. Do not introduce a new mock
   adapter shape unless the existing patterns cannot express what
   the test needs. If a new pattern is needed, factor it into a
   shared test helper rather than duplicating across test files.

Do not touch code outside `orchestra/workflows/`,
`orchestra/templates/`, and (only if needed) `orchestra/api.py`.
Do not modify Slice A or Slice B code unless the validator update
genuinely requires it; if it does, that is fine, but flag the
change in the commit message.

Keep Slice C as one commit. Only split into two if Slice C work
exposes a prior-slice bug under the bug-fix discipline rule, in
which case the prior-slice fix lands as a separate commit on top
of `slice-b-complete` first, retag `slice-b-complete` forward, and
then Slice C's commit lands on top of the new head.

Quality gates: pytest, ruff check, mypy strict all clean before
push.

Standing rules:
- No em-dashes, en-dashes, or semicolons in prose.
- Never mention Claude, Claude Code, or Anthropic in commit
  messages.
- Push after the commit lands.
- Bug-fix discipline per
  `design/orchestra-real-council-plan.md` Conventions section:
  bugs in current slice fixed before commit; prior-slice bugs
  found during current slice get separate fix commits before
  continuing, not folded into the current feature commit.

After the commit pushes and `slice-c-complete` is tagged, stop and
report. Do not begin Slice D.
