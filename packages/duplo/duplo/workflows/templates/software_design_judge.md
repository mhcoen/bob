Judge the proposed design against the query and the review. Verify objections
against the current proposal. Accept only when the engineering reasoning covers
the intended requirements and no unresolved decision blocks implementation.
An empty list of blocking questions does not establish that the design is ready.

Return only JSON with these fields:
decision: accept, iterate, or stuck
feedback: reasons for the judgment, including how review objections were resolved
checks: an object with exactly these boolean fields:
  requirements, architecture, alternatives, data_lifecycle, failures,
  maintainability, verification, unresolved_decisions

Each check concerns the substance of the corresponding design discussion.
Accept requires every check to be true. Use iterate when revision can resolve
the defects. Use stuck when a blocking issue needs evidence or a decision that
the supplied material cannot support. Do not interpret model agreement as proof
of engineering correctness. Make no changes to project files.
The proposal schema in the inputs belongs to the author. Your output must use
the judgment fields specified above. Detailed implementation phases are generated
after design acceptance; judge whether the design supports deriving them.

Review inputs and the author's proposal schema:
{query}

Proposal:
{proposal}

Review:
{review_output}
