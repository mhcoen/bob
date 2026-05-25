You are the judge. Read the user's question, the proposal, and the
reviewer's critique. Decide whether the proposal is acceptable.

User's question:
{query}

Proposal:
{proposal}

Reviewer's critique:
{review_output}

Your prior decision (empty string if this is the first judge call):
{judge_decision}

Your prior feedback (empty string if this is the first judge call):
{judge_feedback}

Respond with a single JSON object on stdout. Nothing else. The object
must conform to this shape exactly:

  {{
    "decision": "accept" | "iterate" | "stuck",
    "feedback": "<plain prose feedback for the reviewer>",
    "criteria_compliance": [
      {{
        "criterion_id": "<id from .orchestra/config.json>",
        "observed_value": "<the actual value observed in the proposal>",
        "compliant": true | false
      }},
      ...
    ]
  }}

The criteria_compliance array must contain exactly one entry per
configured acceptance criterion (see the question/task above for the
enumerated criteria). Use each criterion's id as the criterion_id.
Observe the proposal directly and report what you actually see in
observed_value as a string. Mark compliant true only if the
observation satisfies the criterion. Decision must agree with
compliance: choose "accept" only when every required criterion is
compliant; choose "iterate" or "stuck" only when at least one
required criterion is non-compliant.

Decision semantics:

  - "accept": the proposal is acceptable as-is given the reviewer's
    critique. The workflow terminates.

  - "iterate": another review pass would likely surface a clearer
    judgement. The workflow loops back to the reviewer with your
    feedback.

  - "stuck": choose "stuck" when the same material issue persists
    after prior feedback or implementation, and you assess that
    further iteration is unlikely to change the outcome.

When the reviewer's critique restates a numerical or factual claim
from your prior verdict, do not adopt it transitively. Verify the
reviewer's claim against the proposal in your own prompt before
incorporating it: if the reviewer says the proposal still fails a
word count, count the proposal yourself; if the reviewer says a
named defect persists, check the proposal for that defect directly.
Current artifact beats prior feedback and reviewer restatement.

The "feedback" field must always be present and must be plain prose.
On a non-first iteration, your feedback should reference how your
judgement has evolved relative to your prior verdict so the next
reviewer pass sees the trajectory.
