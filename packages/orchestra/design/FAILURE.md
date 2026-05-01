# Failures

Append-only log of the meta-patterns behind Claude's failures during
the orchestra project. Not a bug list. The point is to track HOW
bugs get created and HOW they fail to be caught, plus the dynamics
of the Claude/ChatGPT review interaction so the user can judge when
review is genuine work and when it has become ritual.

User has explicitly stated: I should not log incidents. I should log
the patterns the incidents reveal. The user does not trust my
opinions on these matters and reads what I write here with that in
mind. The entries are meant to be evidence the user can audit, not
my self-assessment.

Newest entries at the bottom.

---

## Pattern: Trusting "tests pass" as a signal of correctness

**How the bug got created.** Code writes the implementation and the
tests at the same time. The tests inevitably check the surface area
Code chose to wire up, not the surface area the spec required. The
tests reflect the implementer's mental model of what was built, not
the contract the build was supposed to satisfy. When the
implementer's mental model has a gap (snapshot captured but unused;
visibility marked before durability; retry loop in linear path but
not fan-out path), the tests have the same gap. They pass because
they exercise the wrong invariants.

**How the bug failed to be caught.** I treated "203 tests pass,
ruff clean, mypy strict clean" as sufficient signal to declare the
slice done. The signal was load-bearing in my decision to recommend
moving to the next slice. It was wrong-bearing because the test
suite was self-consistent with the implementation rather than
self-consistent with the spec.

**What the correct workflow looks like.** Two separate review
loops, neither substitutable for the other:

1. Plan review: ChatGPT or another reviewer audits the spec against
   correctness invariants before implementation starts. This catches
   semantic gaps, missing failure paths, deterministic-replay
   violations, lock-ordering omissions.
2. Code review: Codex or another reviewer audits the diff against
   the spec after implementation. This catches drift between what
   the spec said and what the code does.

The first loop catches "the spec is wrong." The second catches "the
code does not match the spec." The test suite cannot stand in for
either. A test suite that passes against a correct spec and a
correct implementation is a regression gate; a test suite that
passes without those two preconditions is decoration.

**What was lost.** Until the user explicitly demanded a code review
of Slice A, I would have moved on to Slice B with five concurrency
correctness blockers in the foundation. The cost of catching these
in Slice C or later would have been substantially larger than
catching them now.

---

## Pattern: Forgetting standing rules under pressure

**How the bug got created.** When the user surfaced a fresh problem
mid-conversation (model contamination from a local Claude Code
skill), I generated three candidate fixes quickly. The third
candidate violated a rule the user had already given me about
direct API access vs the Max plan. I generated it because I was
optimizing for "produce a complete-looking option set" rather than
"check the option set against constraints I have been told about."

**How the bug failed to be caught.** I had the rule in memory at
the time. I produced the violating suggestion anyway. The rule was
adjacent to the topic (Claude access) but not exactly on the topic
(skill contamination), and I did not check.

**What the correct workflow looks like.** Before producing any
option set that involves a constraint domain (Claude access,
filesystem boundaries, prohibited tools), check memory for relevant
standing rules. The check is cheap. The cost of missing one is high
because the user reads the violation as evidence that I am not
actually tracking what they tell me.

**What was lost.** The user's confidence that standing rules will
hold across conversational turns. Trust that a constraint stated
once is genuinely loaded into how I generate, not just what I can
recite when asked.

---

## Pattern: Recommending destructive git operations without verifying state

**How the bug got created.** The user wanted to update a file in a
fixed-state test repository and re-run a smoke test. I recommended
`git commit --amend` and `git tag -f` against an assumed base
commit. The repo's HEAD had drifted to a checkpoint commit made by
a prior automated run. My recommendation captured the drift into
the amended commit and moved the canonical tag to the corrupted
state.

**How the bug failed to be caught.** I did not run `git log` before
recommending the amend. The git workflow assumed a state I had not
verified.

**What the correct workflow looks like.** Any git operation that
mutates history (amend, reset --hard, force-push, force-tag) gets a
verification step first: read the current state, confirm it
matches what the operation assumes. Recovery from a corrupted
fixed-state tag is more expensive than the verification.

This pattern is a special case of the larger pattern: confidence
in the state of the world without checking the state of the world.

**What was lost.** Two reset-and-retry cycles before delegating the
recovery to an in-repo Code instance that did the verification I
should have done.

---

## Pattern: Designing without verifying against the user's reference

**How the bug got created.** The user had a pre-existing reference
implementation of the feature being designed (the llm-council skill
in `~/.claude/skills/llm-council/SKILL.md`). I designed a different
shape under the same name. The reference was sitting on disk; I did
not read it before producing the spec.

**How the bug failed to be caught.** Five rounds of plan review
with ChatGPT all operated on the spec I had written, not on the
spec the user actually wanted. ChatGPT cannot catch
"specification-vs-user-intent drift" because ChatGPT does not have
access to the user's reference. Only I do.

**What the correct workflow looks like.** When the user references
a prior implementation, naming convention, or skill, read it before
designing. The cost is one tool call. The cost of not doing it is
multiple rounds of review against the wrong target plus a partial
implementation of the wrong design.

**What was lost.** The original Slice 1 implementation of
`propose_critique_synthesize` is a linear refinement pipeline. It
remains in the codebase but is no longer named `council`. Slice A
of the parallel-execution work plus the council redesign exist
because the original design did not match the user's reference and
neither I nor ChatGPT could have caught that without reading the
reference.

---

## Pattern: Plan review with ChatGPT can become ritual after about three rounds

This entry tracks the dynamic of Claude/ChatGPT plan review during
the council parallel-execution design. The user explicitly does not
trust my opinion on this and asked me to document it anyway as
evidence they can audit. I write this knowing my framing may be
self-serving and the user should treat it accordingly.

**Round 1 (post-spec, pre-implementation).** ChatGPT found 25
issues. Most were genuinely load-bearing: completion-criterion
confusion (committed-artifact vs durable-state_exit), missing
fan-out failure path, log-writer concurrency, anonymization seeding
non-determinism. Without round 1 the implementation would have been
wrong in ways that produce silent data corruption. Round 1 was
clearly worth the cost.

**Round 2.** ChatGPT confirmed all 25 first-round findings either
addressed or no-change-required. Twelve new findings, of which
seven were genuine pre-implementation blockers (fan-out children
following outgoing transitions, cancellation language wrong,
parent-envelope aggregation incoherent, replay rule for failed
groups missing, sibling visibility frozen pre-fan-out, SQLite
connection-thread rule, commit-vs-state_exit ordering). Round 2
caught real second-order issues from round 1 fixes. Worth the cost.

**Round 3.** ChatGPT confirmed seven of round 2's findings closed.
Six were partially closed because I had introduced contradictions
into the plan document during round 2 revisions: the file said one
thing in section 2 and a different thing in section 4. Eleven new
findings, of which five were Slice A blockers, one a Slice B
blocker, three smaller corrections, two confirmations.

The character of round 3's findings shifted. Some were still
substantive (running children must not transition beyond the
fan-out child state; aggregation into parent envelope is wrong
because parent already completed). Others were wording fixes I
should have made cleanly the first time (fan_out_end justification,
drained-success children in outcome map, cancellation registration
shape).

Round 3 was about half genuine review work and half cleanup of
sloppiness in my plan editing. The genuine review work would have
been cheaper if I had not produced contradictory text for ChatGPT
to discover.

**Round 4.** Three of round 3's findings still open. Eleven new
findings, of which five were Slice A blockers (visibility leak from
incomplete children's commits being the most important), one Slice
B blocker, one confirmation, three follow-ups.

The visibility-leak finding is the kind of issue that only shows up
under careful concurrent-execution reasoning. ChatGPT identified
that committed artifacts from incomplete children would remain
visible if cleanup never ran, and that the "producing state name"
key was insufficient (it needed to be "producing invocation"). This
was new substantive work, not cleanup. Round 4 was worth it for
this finding alone.

**Round 5.** ChatGPT confirmed all of round 4's findings closed.
Five remaining pre-implementation blockers, all narrow and
actionable: invocation-vs-state keying, visibility-index source
mechanism, lock-ordering rule, initial-artifact visibility status,
type-checking scope. ChatGPT explicitly recommended stopping
review and starting implementation after these five were folded in.

Round 5's findings were close to the boundary of "genuine review"
and "implementation contract details that don't need pre-flight
review." The reviewer's own self-assessment ("This is now mostly
ready") suggests they noticed the same.

**Inflection point in my own assessment.** Rounds 1 and 2 were
plainly worth doing. Rounds 3 and 4 were worth doing but cost more
than they should have because of my plan-editing sloppiness, which
I would not have caught without ChatGPT pointing it out. Round 5
was at the boundary; the reviewer themselves signaled stopping. A
sixth round would have been ritual.

The pattern: review productivity does not decay smoothly. Rounds 1
and 2 catch design errors. Rounds 3 and 4 catch the second-order
effects of round 1-2 fixes plus my own sloppiness in revising the
plan. By round 5 the reviewer is finding implementation-contract
details that arguably belong in the code review phase, not the
plan review phase.

A reasonable rule: stop plan review when the reviewer's findings
shift from "your design is wrong" to "your contract is
underspecified about implementation choices the implementer can
make." That signal arrived at round 5 in this project.

I do not have confidence in my own judgment on this. The user
should read the rounds for themselves and form their own view. The
strongest evidence I can offer: ChatGPT's round 5 reviewer
explicitly said "stop reviewing the plan and start implementation"
after their own findings were folded in. That is a signal external
to my judgment.

**What this pattern implies for future slices.** Plan review for
Slice B (transform primitive plus anonymize_outputs) is small in
scope. Probably one round of ChatGPT review is sufficient because
the design surface is narrow. Plan review for Slice C
(ask_council.orc plus eleven templates plus role naming) is larger
but mostly composition over Slice A and Slice B; one or two rounds
should suffice. Plan review for any future slice that adds a new
concurrency primitive (nested fan-out, dynamic-N fan-out,
backpressure) probably warrants the full multi-round treatment
because concurrency design is where round 1 review found the most
value here.

Code review with Codex post-implementation is a separate question.
The Slice A code review found six issues, five blockers, that all
five rounds of plan review missed. This is expected: code review
catches drift between spec and code, plan review catches semantic
gaps in the spec. The two cannot substitute for each other and the
balance between them should not shift toward more plan review just
because plan review feels productive in early rounds.

---

## Pattern: Code review against concurrent systems requires multiple rounds for the same structural reason as plan review

This is the code-review-side analog of the plan-review pattern
above. After Slice A's first audit returned six issues with five
blockers, I prescribed seven fixes. After those landed, the second
audit returned four more issues with three blockers. After those
landed, the third audit returned zero blockers and two
acknowledged spec drifts. Three rounds. Eleven prescribed fixes.

**How the bug got created.** Code review of a concurrent system
is bounded by what the reviewer's prompt directs them to look at.
The first-round prompt asked Codex to verify each Slice A
invariant from the plan against the implementation. Codex did
that competently and found five blockers in the paths it was
asked to check. The bugs that hid in the second round (resume
snapshot leaks completed siblings; replay launches pending
children when a completed child has errored; cancellation race
between register and invoke) were in code paths that existed but
were not in the first-round prompt's explicit checklist. The
first round was not negligent; it was scoped.

**How the bug failed to be caught.** Each round of code review
sees only what its prompt enumerates. Concurrent systems have
exponential interaction surface: every new code path multiplies
with every existing code path. A single review round cannot
exhaust the surface even with a careful prompt. Multiple rounds
are necessary, and each round's blockers are bugs that hid in
the interactions the previous round didn't think to enumerate.

**What the correct workflow looks like.** Code review of a
concurrent foundation runs to a fixed point: keep auditing until
a round returns zero blockers. The decision to stop is the
reviewer's, not the implementer's: the reviewer is the only party
with the cognitive distance to recognize when the remaining
findings are genuine spec drift (acceptable as follow-up) versus
unenumerated interaction paths (still bugs).

In this project, three rounds reached fixed point. Codex
explicitly stated zero blockers on round three and recommended
greenlight. That external stopping signal is what makes the
fixed-point condition observable; without it, I would not know
whether round four would find more blockers or not.

**What was lost.** Nothing concrete on the project, because the
fixed point was reached. But it is worth noting that each round
cost roughly one Codex audit cycle plus the implementer's fix
work. Two extra rounds beyond the first. The cost of skipping
those rounds would have been catching the eleven fixes' worth of
bugs in Slice C or later, when the foundation has been built on.

**Difference from plan review.** Plan review of design documents
can spiral into ritual at round four or five because the
reviewer starts finding implementation-contract details that
belong in the code review phase. Code review of a concurrent
system does not have this failure mode the same way: each round
either finds blockers or doesn't, and "the reviewer is finding
things that don't matter" is unlikely because the reviewer is
looking at running code, not at a description of intended
behavior.

The asymmetry: plan review has diminishing returns because the
plan is bounded; code review has more durable returns because
the code's interaction surface is unbounded.

---

## Pattern: Failing to update persisted memory when a standing rule is refined conversationally

**How the bug got created.** The user established a standing rule
(log failures to a file). I recorded it in memory in its initial
form. The user then refined the rule conversationally ("not
incidents, only structural meta-patterns; cite external evidence
over self-assessment"). I treated the refinement as integrated
into my behavior for the current session but did not update the
memory entry. The persisted memory still reflected the original
rule. In a future session without the conversational context, I
would have followed the original rule and produced incident-style
entries the user explicitly does not want.

**How the bug failed to be caught.** Memory entries are
write-once-and-trust by default. I do not routinely re-check
memory entries against more recent conversational context to look
for staleness. The user had to ask "are you still doing this the
way I told you" before I checked.

**What the correct workflow looks like.** When a user refines a
standing rule mid-conversation, the refinement triggers a memory
update, not just a behavior change. The two are different: a
behavior change applies to the current session, a memory update
applies to all future sessions. If the user expects the
refinement to be permanent (and refinements to standing rules
almost always are permanent), the memory entry must be replaced.

The cheap operational rule: any time the user uses phrasing like
"actually," "no, what I meant was," "I'm not interested in X,
I'm interested in Y," or anything that revises a previously-
stated standing rule, the next action is `memory_user_edits
replace`, not just an updated reply.

**What was lost.** Almost nothing concrete this session because
the user caught it. But the pattern is structural: any
conversational refinement that I integrate into current behavior
without persisting can revert the next time the session ends.

---

## Pattern: Original-design audit rounds underweight crash-window enumeration in concurrent replay paths

This is a refinement of the prior code-review pattern. After Slice A
reached fixed-point under its own audit (zero blockers on round
three), three further rounds of audit during Slice B work surfaced
additional Slice A bugs that the original audit had not found. The
bugs were not in the round-by-round fix commits introduced during
Slice B; they were in the original Slice A code that round three of
Slice A's own audit had cleared.

**How the bugs got created.** Slice A introduced a fan-out
primitive with multiple replay rules covering the crash window
between any two adjacent durable log records. The implementer's
mental model enumerated some of these windows (the listed cases in
the spec) and produced replay code for them. Other windows existed
in the actual implementation as a consequence of the chosen log
record ordering but were not enumerated as cases the spec called
out. Specifically: (a) crash between live `fan_out_end` and parent
`transition` (replay never wrote the missing parent transition; the
replay layer papered over the gap by treating `fan_out_end`'s target
as if it came from a transition), and (b) resumed `fan_out_end`
records carrying `attempt=None` because the parent attempt from
`fan_out_start` was lost during replay-state reconstruction and
never threaded through `cmd_resume` to `resume_fan_out`.

**How the bugs failed to be caught.** Slice A's three audit rounds
operated on the spec's enumerated replay cases. The cases the spec
enumerated were the cases the audits checked. The two unenumerated
crash windows were structurally inevitable consequences of the log
record schema (any pair of adjacent records produces a crash window)
but the spec did not list them and the audits did not derive them
independently from the schema. The first audit round of Slice B,
operating against a different but adjacent surface (transform
state semantics), did not surface them either. The second and third
Slice B audit rounds, prompted to look harder at the replay path
because earlier rounds had found bugs in it, derived the missing
crash windows from the schema and surfaced both bugs as blockers.

**What the correct workflow looks like.** For any concurrent
foundation that introduces a new log record schema, the audit prompt
must include an explicit derivation step: enumerate every adjacent
pair of durable records in the schema, list the crash window between
them, and verify replay closes each window. The spec's listed cases
are necessary but not sufficient because the spec author's
enumeration is bounded by the same mental model that produced the
implementation. The audit must derive the cases independently from
the schema, not check the spec's cases against the implementation.

This applies specifically to fan-out crash windows in this project:
`fan_out_start -> child events`, `child events -> fan_out_end`,
`fan_out_end -> parent transition`, plus the open-fan-out resume
path's parallel structure. Each pair is a crash window. Each must
be closed by replay. Each must be tested with a truncation point
between the two records.

The broader pattern: replay audits should treat the log schema as
the source of truth for what crash windows exist, not the spec's
enumeration of cases. The spec describes intended behavior; the
schema describes the durable structure that replay actually has to
close over.

**What was lost.** Three additional audit rounds during Slice B
work to find Slice A bugs. Each round cost a Codex audit cycle plus
the implementer's fix work. The fixes themselves were small once
identified. The cost was the audit cycles, not the code changes.
The alternative cost (catching these bugs in Slice C or later when
built on) would have been higher, but the original Slice A audit
could have caught them with a schema-derived crash-window
enumeration in its prompt and saved the three rounds.

**External evidence.** Round three of Slice B's audit explicitly
flagged that its blockers were in original Slice A replay code, not
in the round-one or round-two Slice B fix commits. That attribution
is the reviewer's, not mine.

---

## Pattern: Schema-derived crash-window enumeration in audit prompts produces zero-blocker rounds when applied prospectively

This is an N=1 follow-on data point validating the prior entry's
prescription. Slice C audit round one applied the prior entry's
prescription as section 5c of the audit prompt: explicitly asked
the reviewer to enumerate new adjacent log-record pairs introduced
by the council workflow (transform `state_exit` followed by the
second fan-out's `fan_out_start` was the obvious new adjacency)
and verify replay closes each crash window. The reviewer simulated
crashes in the new windows and confirmed they close correctly.
Round one returned zero blockers, in contrast to Slice B's three
rounds.

**What this validates.** The prior entry hypothesized that an
audit prompt asking the reviewer to derive crash windows from the
log schema rather than from the spec's enumeration would catch
new surface before the audit rather than after. Slice C round one
is the first audit where this prescription was applied
prospectively, and it produced zero blockers and a confirmed
crash-window simulation as observation rather than a finding.

**What this does not validate.** N=1 is not strong evidence. The
prescription could have produced the same result by chance because
Slice C is a smaller surface than Slice B (one workflow file plus
eight templates plus a small registration site, versus a new
primitive plus a registry plus replay semantics). The next data
points worth tracking: does the prescription continue to produce
clean rounds when applied to surfaces of comparable complexity to
Slice B, and does it surface schema-adjacent bugs that the spec's
enumeration did not list.

**Operational conclusion.** Continue including a schema-derived
crash-window enumeration request in every audit prompt for slices
that touch the log schema or the replay path. The cost is one
paragraph in the prompt. The expected return is catching original
design bugs at the audit step rather than at the audit-of-the-next
slice step.

**External evidence.** Slice C round one's report listed the
crash-window simulation under "Observations" item 4, not under
blockers or non-blocking issues. The reviewer treated the
verification as confirmation of correct behavior, which is the
outcome the prescription was designed to produce.

---
