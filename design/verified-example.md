# Independently verified change: example contract

Slice C starts with an inclusive-range counting CLI: `span.py START END` prints
the number of integers in the range, including both endpoints, or zero when
START exceeds END. Inputs are decimal integers. The fixture deliberately omits
one endpoint. Its existing regression tests cover reversed ranges only.

The example harness owns requirement `REQ-SPAN-001`, task `T-EX-000001`, and a
separate oracle. Candidate edits may replace only the CLI, its project tests, and
its README. The oracle checks explicit behavioral cases through the CLI process,
including singleton, negative, crossing-zero, and reversed ranges. Passing project
tests alone never completes the requirement.

The harness composes the real planfile, McLoop execution ownership, completion
receipt, acceptance-command, Git, and ledger APIs. It is a small reference
orchestrator, not a test of every branch in McLoop's full scheduling loop or of
the Duplo discovery pipeline. Deterministic editors make one attempt each: correct,
plausibly wrong, weakened tests, no-op, and unrelated documentation. An explicitly
selected external editor command can return candidate files as JSON; default runs
make no model calls. Mock results do not measure model competence.

Acceptance runs against an exported, staged Git tree, outside the editable
project. The tree and oracle digests bind the evidence to the bytes checked.
The harness records baseline and candidate commit identities, check commands,
outputs and return codes, oracle identity, task and requirement identity, receipt
and ledger references, and explicit unavailable usage data. These local records
are inspectable evidence, not signed attestations or a security sandbox against
a hostile process running under the operator's account.

The interruption demonstration exits the worker immediately after the candidate
commit lands, before its completion receipt records the return. Restart must
refuse to edit. The example's explicit reconciliation procedure verifies parent
and tree identity, repeats the independent checks, records the landed commit,
advances the plan, emits the ledger event, and acknowledges the abandoned
execution receipt. A subsequent resume sees a completed task and does not invoke
an editor or make another implementation commit. This procedure belongs to the
example; it does not imply automatic reconciliation in ordinary McLoop runs.

Deliverables: executable walkthrough, preserved JSON/log evidence, adversarial
and process-interruption regressions, a CI gate, and documented commands for
success, rejection, recovery, and the opt-in external editor.

Reliability baseline: commit `c64c34fe` passed all five hosted jobs in
[run 34009350618](https://github.com/mhcoen/bob/actions/runs/34009350618), including
7,050 workspace tests, with 136 skipped. That result predates this example.
