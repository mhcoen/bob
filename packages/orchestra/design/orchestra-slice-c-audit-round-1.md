# Slice C audit round 1

Slice C of the orchestra real-council work landed at commit `80441c8`,
tagged `slice-c-complete`, on top of `slice-b-complete` (`31357d2`).
Repo is at `/Users/mhcoen/proj/orchestra`. The slice spec is Section
4 (workflow), Section 5 (templates), and Section 3 (role naming) of
`design/orchestra-real-council-plan.md`. The kickoff doc is
`design/orchestra-slice-c-kickoff.md`.

Audit Slice C against the plan and kickoff. Treat this as a
blocker-finding pass, not a style review. Report findings as
numbered blockers with file:line references.

Slice C is the first slice that exercises both Slice A's fan-out
primitive and Slice B's transform primitive together end-to-end. The
audit surface includes the workflow file, eight templates, the
`anonymize_outputs` registration site in `orchestra/api.py`, and 15
new tests. Pattern from Slice B's three audit rounds suggests this
audit may also surface original Slice A or Slice B bugs that the
end-to-end exercise reveals for the first time.

Three design decisions Code flagged in the round-0 report that need
explicit rulings during this audit:

- The terminal state is named `synthesize` rather than `chairman`
  because phase 4 of the loader's validator enforces a single
  namespace across states, artifacts, models, roles, agents, and
  groups. The role binding remains `chairman` per Section 3. Plan
  Section 4 calls the state `chairman`. Rule on whether this rename
  is the right contract going forward, or whether the namespace
  check should be relaxed for state-name/role-name overlap.

- The reviewer template renders `{anon_map}` via Python's default
  `dict.__str__`. This relies on CPython's insertion-order iteration
  (guaranteed since 3.7) and on `anonymize_outputs` building the
  dict left-to-right. The determinism test pins the behavior
  end-to-end. Rule on whether to leave this implicit dependency or
  require an explicit formatter in the template renderer.

- `anonymize_outputs` is registered with the council's exact
  five-advisor input schema, not a generic shape, so the phase-5
  validator catches a workflow that adds or removes an advisor name.
  Code exposes `_ASK_COUNCIL_ANONYMIZE_INPUT_SCHEMA` as a
  module-level constant the determinism test pins. Rule on whether
  this tightening is correct or whether it creates a coupling that
  Slice D or later will have to break.

Focus areas:

1. Workflow file (`orchestra/workflows/ask_council.orc`).

   a. Verify the workflow declares thirteen states matching Section
      4: `frame`, five advisor states (`contrarian_advise`,
      `first_principles_advise`, `expansionist_advise`,
      `outsider_advise`, `executor_lens_advise`), `anonymize`, five
      reviewer states (`reviewer_1` through `reviewer_5`), and one
      terminal state (named `synthesize` per the design decision
      above).

   b. Verify the first fan-out spawns the five advisor states with
      `join anonymize` and `on error stop`. Verify the second
      fan-out spawns the five reviewer states with `join synthesize`
      (or whatever the terminal state is named) and `on error stop`.

   c. Verify the `frame` state's `reads` references the external
      inputs (query and history) and `writes` declares
      `framed_question`. Verify each advisor state's `reads`
      references `framed_question` and `writes` declares its
      lens-named output. Verify `anonymize` reads all five advisor
      outputs and writes `anon_map` only (no `deanon_map`,
      consistent with Section 2). Verify each reviewer state reads
      `anon_map` and writes its `review_N_output`. Verify the
      terminal state reads `framed_question`, all five named
      advisor outputs, and all five reviewer outputs, and writes
      `chairman_output`.

   d. Verify the workflow comment documents the `on error stop`
      choice with the rationale (failed council fan-outs stop the
      run rather than synthesizing partial output) per the kickoff.

   e. Verify role bindings: `frame` uses `framer`, each advisor
      uses its corresponding lens role, each reviewer uses
      `reviewer`, the terminal state uses `chairman`.

   f. Verify the workflow's actor-kind declarations: text-role for
      all states except `anonymize`, which is `transform`.

2. Templates (`orchestra/workflows/templates/`).

   a. Verify eight template files exist with the expected names per
      the kickoff.

   b. Verify the framer template takes query and history and
      produces a clear neutral framed question.

   c. Verify each lens advisor template contains the lens
      description, the framed question, and the "lean fully into
      the angle, 150-300 words" instruction. Cross-check the lens
      descriptions against `/Users/mhcoen/.claude/skills/llm-council/SKILL.md`
      for fidelity to the reference.

   d. Verify the reviewer template references only `anon_map` and
      contains none of the strings `contrarian`, `first_principles`,
      `expansionist`, `outsider`, `executor_lens`, or any other
      lens identifier. This is a workflow contract per Section 5.

   e. Verify the chairman template references the framed question,
      every named advisor output, and every reviewer output, and
      pins the verdict structure with the exact Section 5 headers:
      Where Council Agrees, Where Council Clashes, Blind Spots
      Caught, Recommendation, One Thing to Do First.

   f. Verify all five reviewer states use the same template file
      (the kickoff specifies a shared reviewer template).

3. Validator and `orchestra/api.py` registration.

   a. The kickoff says role-binding validation lives in
      `orchestra/api.py` and should be reused if sufficient. Code
      reports the existing `_validate_role_bindings` accumulates
      one `ConfigError` per missing binding into a single combined
      error. Verify this is correct: the all-bindings-missing test
      is reported as enumerating every required role in a single
      error, and the per-binding-missing tests are reported as
      naming the specific missing role. Spot-check the error
      messages for accuracy.

   b. Verify the additive change to `orchestra/api.py` registering
      `anonymize_outputs` with the council schema in both
      `_pre_load_registry` and `_build_registry`. Verify the
      registration is gated on `"anonymize_outputs" not in
      reg.transforms` so it is a no-op for tests that build their
      own registry with a different shape. Verify this gating does
      not silently mask a registration conflict in production usage.

   c. The exposed module-level constant
      `_ASK_COUNCIL_ANONYMIZE_INPUT_SCHEMA` is used by the
      determinism test to keep the schema and the mock data in
      sync. Verify the constant is the single source of truth for
      the schema (no parallel hardcoded list in the registration
      site or elsewhere).

4. End-to-end test correctness (`tests/test_workflows_ask_council.py`).

   a. The chairman prompt assembly test asserts the rendered prompt
      carries every advisor output text, every reviewer output
      text, and the framed question. Verify the test inspects the
      actual prompt the adapter receives, not the workflow's
      structural reads. Verify the mock advisor outputs are
      non-overlapping placeholder strings so the assertion is
      meaningful (Code reports ALPHA-RESPONSE-DOWNSIDE,
      BETA-RESPONSE-RECAST, etc.; spot-check this).

   b. The reviewer prompt isolation test asserts `anon_map` A
      through E letter keys are present (pinned with the colon
      character to avoid single-letter false matches) and that
      none of the lens identifier strings appear. Verify the
      pinning is robust: the test should fail if a future change
      causes a lens identifier to leak into the reviewer prompt
      via any path (template change, transform change, prompt
      assembly change).

   c. The reviewer statelessness test asserts distinct
      `prepared.inner` objects per call (id-distinct), role binding
      `reviewer` on every call, attempt=1 on every call, and no
      extra session-shaped fields in the inner. Verify the
      id-distinctness check is meaningful (the adapter does not
      recycle `prepared.inner` objects in a way that would make
      id-distinctness a false positive). Verify the
      session-shaped-field check covers the actual fields that
      would indicate a session continuation (continuation token,
      session id, conversation id, or whatever the text-role
      adapter's session-mode protocol uses; if it does not
      support session mode at all, verify the test still pins
      that absence).

   d. The validator-rejection test is parametrized over the eight
      required roles, removing each in turn. Verify the
      parametrization covers all eight and that each case asserts
      the error message names the specific missing role. Verify
      the all-bindings-missing test enumerates every required
      role in a single error.

   e. The end-to-end determinism test asserts byte-identical
      `anon_map` and chairman prompt across two runs with the
      same `(run_id, query, history)`. Verify the test uses the
      same advisor texts across both runs (otherwise the
      determinism would not be testable through the seed).
      Verify the byte-identity assertion is on the actual rendered
      prompt strings, not on a structural representation.

5. Cross-cutting: end-to-end exercise of Slice A and Slice B.

   a. The end-to-end test runs both fan-outs (advisors and
      reviewers) and the transform between them. Verify the test
      exercises the visibility rule (chairman's reads see all
      five advisor outputs because their producing invocations
      have durable success state_exit). Verify the snapshot
      semantics are exercised (reviewers' snapshots include
      anon_map but not other reviewers' outputs).

   b. The determinism test exercises Slice B's seed contract
      through the full council. Verify the seed inputs to
      `anonymize_outputs` match what the transform actually
      receives at runtime: `(run_id, state_name="anonymize",
      sorted_input_keys)` where `sorted_input_keys` is the
      lex-sorted list of the five advisor output names.

   c. Look for any latent crash-window in the end-to-end path that
      Slice A's audit rounds did not enumerate. The replay-path
      audit pattern in `design/FAILURE.md` calls out that audits
      should derive crash windows from the log schema, not from
      the spec's enumeration. The new end-to-end exercise creates
      adjacent log records that may not have been audited as
      adjacent pairs before (e.g., transform `state_exit` followed
      by the second fan-out's `fan_out_start`). Enumerate any new
      adjacent pairs introduced by the council workflow and verify
      replay closes each crash window. If any pair is not closed,
      flag as a Slice A blocker (not a Slice C blocker, per
      bug-fix discipline).

6. Cross-cutting: invariant preservation.

   a. Run pytest, ruff check, and mypy strict at the
      `slice-c-complete` head and confirm they pass.

   b. Verify no Slice A or Slice B test was modified by the Slice C
      commit. The kickoff allowed touching `orchestra/api.py` if
      necessary; verify the change is the minimal additive one
      Code described and does not silently weaken any existing
      invariant test.

   c. Verify the Slice C commit at `80441c8` does not contain
      prior-slice fixes that should have been split out per
      bug-fix discipline.

7. Bug-fix discipline and tag hygiene.

   a. `slice-c-complete` was tagged at `80441c8` on top of
      `slice-b-complete` (`31357d2`). Verify the tag is at the
      correct commit and the commit's parent is `31357d2`.

   b. `main` was force-pushed to `80441c8`. Verify this matches
      the slice-c-complete tag.

8. Quality gates. pytest 278 passed, ruff clean, mypy strict clean
   across 37 source files are reported. Spot-check by running them.

9. Design decisions: explicit rulings.

   a. `chairman` -> `synthesize` state rename. Is this the right
      contract going forward, or should the namespace check be
      relaxed? If accepted, Section 4 of the plan should be updated
      to match (this is a follow-up doc edit, not a Slice C
      blocker).

   b. `dict.__str__` rendering of `anon_map` in the reviewer
      template. Acceptable for Slice C with the determinism test
      pinning the behavior, or does it warrant an explicit
      formatter to remove the implicit dependency on CPython's
      insertion-order iteration?

   c. Council-specific `_ASK_COUNCIL_ANONYMIZE_INPUT_SCHEMA` vs a
      generic `anonymize_outputs` registration. Is the tightening
      correct, or does it create a coupling that Slice D or later
      will have to break?

10. Meta-question. The audit pattern across Slice B was that each
    round found bugs in earlier slices. If this round finds new
    Slice A or Slice B bugs not introduced by the Slice C commit,
    note them explicitly so the pattern can be tracked. Per the
    `FAILURE.md` entry on crash-window enumeration, any such
    findings in the replay path are evidence the original Slice A
    audit's enumeration was still incomplete.

Return findings as a numbered list grouped by severity: blockers,
non-blocking issues, observations. For each blocker, name the file
and line and state what the fix should be.
