# Design review limits

Duplo's design command uses a finite review sequence. An existing proposal
enters review directly. A new project needs one author call first. Review findings
receive stable identifiers and an explicit disposition from the judge. An author
can submit one patch against the candidate's digest. A follow-up review checks
the patch and its consequences. Unresolved blocking findings prevent publication.

The patch replaces named sections or decisions. Duplo checks its base digest and
references before applying it to a copy. Requirement coverage and the complete
result must still validate. Earlier attempts and call receipts preserve the source
proposal and patch. The current attempt stores the resulting design. The final
judgment sees the full candidate so changes across components remain reviewable.

Planning uses one proposal for the complete phase sequence and one independent
review. Duplo validates the canonical plan and scope coverage before review and
again before publication. A rejected plan stays in the receipts. No phase starts
its own author/reviewer/judge cycle through this command.

Each project has a persistent allowance for model invocations and submitted
prompt bytes. Reservations are written before invoking a provider. Interrupted
calls consume their reservation. Repeating the command does not replenish the
allowance. An explicit CLI option starts a new allowance and preserves previous
receipts. Design and plan generation share the allowance.

The text CLI adapters do not currently enforce generation token limits or report
token usage. A token cap must therefore fail before provider execution. Prompt
byte limits measure only the submitted prompt. They exclude provider overhead,
reasoning, and tool activity. A returned-output size check rejects an oversized
answer after generation; it cannot limit the cost of producing that answer.

Tests use scripted responses. They exercise reservation failures, interrupted
calls, stale patches, finding dispositions, and publication prerequisites.
These checks establish the tested bookkeeping and validation behavior. They do
not establish the soundness of a generated design.
