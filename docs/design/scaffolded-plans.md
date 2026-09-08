# Scaffolded implementation plans

A scaffold demonstrates a small path through the intended program. Compilation
and isolated component tests remain necessary, but cannot substitute for that
path. Simulated dependencies must be named and progressively replaced as their
implementations become available. The accepted software design remains authoritative.

Plans express milestones as ordinary, independently executable AUTO tasks. A
milestone carries `milestone: scaffold` or `milestone: integration`, a
`demonstrates` outcome, `simulated` dependencies, and `replaces` information.
Its `accept: command-exit: ...` annotation supplies the exact command. Commands
must exercise the assembled system and fail for missing prerequisites or
inconclusive results. Duplo's independent reviewer assesses that semantic claim.
Bob's structural validator cannot establish it from a command string.

New Duplo plans require milestones. Existing plans retain their behavior until
an explicit revision adopts the contract. Adoption begins at the first phase
containing a milestone; every earlier task must already be complete. The first
milestone is a scaffold. Every phase from adoption onward ends in a milestone.
A completed milestone cannot follow unfinished work; new work needs a later
milestone. Earlier milestones may appear within a phase to expose integration problems
before further implementation. Milestones cannot be batched or delegated to a
human confirmation prompt. McLoop executes them using its existing AUTO command
path, which stops on failure and records the result before advancing.

Revision preparation holds McLoop's project lock, refuses unresolved completion
receipts, and validates a candidate through the shared typed plan APIs. It writes
a candidate, a diff and a receipt under `.mcloop/plan-revisions/`. The receipt
binds the original plan, Git revision, project files and orchestration state.
Preparation never changes PLAN.md. Applying a named receipt is a separate,
explicit operation and refuses stale inputs. Completed tasks retain their IDs,
content, status and phase ownership. Existing tasks cannot be removed, and new
tasks must be pending. Existing phase identities and task-to-phase assignments
are preserved; phase splits and cross-phase moves still belong to Duplo's
ledger-aware reauthoring path. This operation supports insertion and sequencing
within those boundaries and retains its own before/after revision history.

`duplo revise-plan` generates the candidate. It reads the accepted design and
current execution checkpoint and asks the configured author for bounded JSON task
operations. The runtime assigns new IDs and constructs a candidate through the
typed plan APIs. GLM reviews the proposed operations through OpenRouter. A rejected
or structurally invalid proposal gets one correction round. The command retains
call results and can resume without repeating completed calls. A successful
candidate enters the same stopped-plan staging mechanism described above.

The default objective introduces an application scaffold and phase integration
milestones. A supplied objective can revise pending work within the accepted
contracts. This command preserves phase ownership; the ledger-driven reauthoring
path still owns phase splits and cross-phase moves. Applying a staged proposal is
an explicit operation. Neither generation nor model approval reapproves the design
or establishes implementation correctness.
