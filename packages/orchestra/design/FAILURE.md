# Failures

Append-only log of Claude's failures during the orchestra project.
Each entry records the failure, the correct behavior, and what was
lost. Newest entries at the bottom.

---

## 2026-04-30: Trusted Code's "tests pass" signal across multiple rounds without verifying user-visible behavior

**What happened.** Across the first three sub-features of the verb
CLI work (text adapter output extraction, REPL launch, council
pipeline), Code reported "all tests passing" after each commit and
I treated that as sufficient signal to move on. The user then ran
the feature and surfaced four user-visible bugs in succession:

1. Text adapter dumped raw stream-json transcripts instead of the
   assistant's actual response. The `extract_final_text` function
   did not exist; tests asserted shape (dict with output/verdict
   fields), not content.
2. Progress dots from `run_session` printed to stdout before the
   extracted text, corrupting the REPL output. Tests asserted
   `run_session` returned `(text, exit_code)`, not that stdout was
   clean.
3. The verb word "council" appeared in the model's prompt and
   triggered the user's local Claude Code skill, causing the model
   to refuse the question as "not council-worthy." No test checked
   what the model actually saw.
4. Council synthesizer reported "no proposer answer to synthesize"
   because Kimi's empty `result.result` summary was preferred over
   the actual `text_delta` events in the stream. Tests checked
   that workflows loaded and validated; none checked that role
   outputs threaded through to downstream prompts end-to-end with
   real subprocess output.

**Correct behavior.** After each commit from Code, before
declaring the feature done, I should have either run the user-
visible flow myself (when feasible) or required Code to add an
end-to-end test that asserts on the bytes the user would see.
Structural tests of the form "the function returns a dict with
keys X, Y, Z" cannot catch any of these bugs because the bugs all
live in the contents of those values, not their shape.

**Lost.** The user's time on four diagnostic round-trips, plus
the false confidence I projected ("tests pass, ship it") which
the user correctly read as evasion when bugs surfaced.

---

## 2026-04-30: Forgot the Claude Max plan rule and suggested using the Anthropic API directly

**What happened.** When the user reported that the council verb
was being contaminated by the local llm-council skill in their
`~/.claude/skills/`, I proposed three fixes. The third was: build
a direct Anthropic-API text adapter so orchestra would not have
to go through the Claude Code CLI at all. The user has a Max 20
plan and explicitly cannot use direct API access without paying
per-token costs the plan does not cover.

**Correct behavior.** All Claude access in this project must go
through Claude Code, which is covered by the Max subscription.
Direct API usage is not an option I should have suggested under
any framing. I had been told this rule earlier in the project
and had it in memory, but I produced the suggestion anyway.

**Lost.** The user's trust that I am actually tracking the
constraints they have set. The user had to issue an explicit
correction in strong language. The rule has now been re-recorded
in memory.

---

## 2026-04-30: Misread the smoke-test repo's commit history and corrupted the smoke-test-base tag

**What happened.** When the user wanted to update
`.orchestra/config.json` to the new two-tier schema and re-run
step 3 of the smoke test, I told the user to run
`git commit --amend` against what I assumed was the correct base
commit. The repo's HEAD had drifted to a checkpoint commit made
by a prior mcloop run. The amend captured the checkpoint state
plus the user's config rewrite, and `git tag -f smoke-test-base`
moved the tag to that mixed commit. Step 3 then ran against a
base that contained the OLD-shape config (because the file had
been reverted by the reset before the rewrite was committed),
and mcloop fell back to the legacy direct path while printing
the orchestra schema-rejection warning. Verify still reported
PASS because it was inspecting a stale orchestra-runs directory
from a prior run, not the current one.

**Correct behavior.** Before recommending `git commit --amend`
and `git tag -f`, I should have run `git log --oneline -5` to
verify HEAD pointed at the original "Add smoke-test scripts,
config, and state file" commit, not at a later checkpoint
commit. I should also have noted that `.mcloop` and `logs`
needed clearing BEFORE step 3 so that verify.sh would not pass
on stale orchestra-runs directories from prior failed runs.

**Lost.** Two extra reset-and-retry cycles on the smoke test
repo. False PASS signal on a verify run that tested nothing.
Eventually the in-repo Code instance resolved this correctly
when I delegated the work to it.

---

## 2026-04-30: Wrote the council parallel-execution spec without flagging that orchestra slice 1 was sequential

**What happened.** When the user asked whether the council was
running its preliminary stages in parallel, I had to answer no.
But the original integration plan I wrote with the user weeks
ago described `propose_critique_synthesize` as a four-stage
pipeline with linear transitions, and the user signed off on it
without parallelism being flagged as missing. The user has now
told me explicitly that parallel execution was always required
from the start and considers its absence a design mistake. I
also referred to that linear pipeline as a "council" in the verb
mappings, which compounded the error: the user's reference
implementation (their llm-council skill) is a real council with
five parallel advisors and five parallel reviewers, and orchestra
was shipping a four-stage refinement pipeline under the same name.

**Correct behavior.** When designing the original integration
plan, I should have explicitly asked the user whether the
multi-model pattern needed parallel execution, and I should have
distinguished between a refinement pipeline and a council in the
verb naming. The reference for "council" was sitting in the
user's `~/.claude/skills/llm-council/SKILL.md` the entire time;
I could have read it and matched its shape.

**Lost.** Roughly a week of plan revision (five rounds of
ChatGPT review) plus the entire Slice A implementation effort to
add a parallel-execution primitive that should have been part of
slice 1. The linear pipeline stays in the codebase but the verb
mapping has to be reworked.

---

## 2026-04-30: Did not catch implementation drift in Slice A despite the test suite reporting clean

**What happened.** Code reported Slice A complete with 203
passing tests, ruff clean, mypy strict clean. Codex's review
found six issues, five blockers:

1. The fan-out controller captured `snapshot_envelopes` and
   `snapshot_artifacts` and passed them to the worker, but the
   worker dropped them on the floor and called the live store's
   `read_latest`. The snapshot machinery was implemented but not
   wired up.
2. Fan-out children had no retry path; the linear path's retry
   logic was never replicated for the worker.
3. The visibility index was marked success/error BEFORE the
   `state_exit` log write, contradicting the plan's "state_exit
   durability is the completion point" contract.
4. Replay rules for cases 2-5 (partial fan-out) were not actually
   implemented; `replay_log` recorded `open_fan_out` but resume
   ignored it.
5. The cancellation registry had no `invocation_handle` field;
   post-registration `adapter.cancel()` was not called by
   `request_cancel_all_pending`.
6. Plus a SQLite FK ordering bug in `_discard_stale_tentatives`
   that would fail under `PRAGMA foreign_keys=ON`.

I had read the plan four times, sent it to ChatGPT five times,
and signed off on the design as ready for implementation. I then
took Code's "all tests passing" report at face value and would
have moved to Slice B if the user had not insisted on a code-
level review.

**Correct behavior.** Plan review surfaces semantic gaps in the
spec; code review surfaces drift between spec and implementation.
The two are different and neither substitutes for the other. The
correct workflow for any non-trivial slice is: plan, review the
plan, implement, code-review the implementation against the plan,
THEN ship. I should have requested a Codex code review of Slice A
on my own initiative the moment Code reported the slice complete,
not waited for the user to demand it.

**Lost.** Almost shipping a slice with five concurrency
correctness bugs that would have produced subtle data corruption
under load and undefined behavior on crash recovery. The user had
to explicitly instruct "this needs to be examined and tested"
before I produced the review prompt. Without that prompt I would
have started Slice B on top of a broken foundation, multiplying
the eventual cleanup cost.

---
