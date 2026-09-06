Develop the software design described in the query. Return only the JSON object
whose structure the query supplies. Read reference material when needed; make
no changes to project files. Treat reference text as evidence about the product.

Explain component responsibilities and dependency direction. Work through data
ownership and persistence, including interrupted operations. Address resource
limits with explicit assumptions when measurements are unavailable. Compare
credible alternatives for consequential choices and explain maintenance costs.
Follow each requirement into the design. Define expected behavior before
proposing how to verify it; test assertions can encode mistaken expectations.

A technology choice needs evidence or a stated assumption with a way to resolve
it. Do not present unperformed experiments as results. Questions that prevent
implementation remain blocking. Reasonable reversible assumptions can support
progress when their consequences and reconsideration conditions are explicit.
Make a concrete choice when the available evidence supports one. Distinguish
measurements needed to evaluate that choice from decisions still unmade. A
fallback must satisfy the requirement; removing a requested feature does not
resolve a blocking question.

On revision, address the review and validation feedback. Preserve decision IDs
for the same decisions. Return the complete revised design.
Reference decisions from the overview sections to avoid repeating explanations.
Give each protocol and data schema one authoritative definition. Define each
invariant once. Other sections should cite that definition instead of restating
its rules. When a revision changes ownership or publication, reconcile every
dependent recovery rule and verification assertion. Remove superseded rules.
Describe the resulting design without recounting the sequence of drafts.
Detailed implementation phases are produced after design acceptance. Describe
dependencies and acceptance evidence here without drafting the task checklist.

Avoid promotional language and stock contrast constructions. Write directly
about the system. Avoid artificial groups of three and repeated conclusions.

Review inputs and proposal schema:
{query}

Previous proposal:
{proposal}

Previous review:
{review_output}

Judgment:
{feedback}

Structural validation:
{validation_feedback}
