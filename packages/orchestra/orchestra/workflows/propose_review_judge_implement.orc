spec 0.1

workflow propose_review_judge_implement

  external_input task text
  external_input project_dir text
  external_input history text

  max_total_steps 200

  model m_proposer
  model m_reviewer
  model m_judge
  model m_implementer

  agent implementer_agent
    model m_implementer
    adapter claude_code_agent
    context_policy fresh

  artifact framing text
  artifact review_output text
  artifact judge_verdict json
    schema "schemas/prji_judge_verdict.json"
    extract decision => judge_decision text
    extract feedback => judge_feedback text
    extract fix_instructions => fix_instructions text
  artifact judge_decision text
    initial ""
  artifact judge_feedback text
    initial ""
  artifact fix_instructions text
    initial ""
  artifact implementer_output text
    initial ""

  role proposer
    prompt template "templates/prji_proposer.md" with task, history, judge_decision, judge_feedback

  role reviewer
    prompt template "templates/prji_reviewer.md" with task, framing, judge_decision, judge_feedback, implementer_output

  role judge_role
    prompt template "templates/prji_judge.md" with task, framing, review_output, implementer_output, judge_decision, judge_feedback

  role implementer
    prompt template "templates/prji_implementer.md" with fix_instructions, project_dir

  state propose
    actor model m_proposer
    role proposer
    reads task, history, judge_decision, judge_feedback
    writes framing text
    on complete => review
    on error => stop
    on timeout => stop

  state review
    actor model m_reviewer
    role reviewer
    reads task, framing, judge_decision, judge_feedback, implementer_output
    writes review_output text
    on complete => judge
    on error => stop
    on timeout => stop

  state judge
    actor model m_judge
    role judge_role
    reads task, framing, review_output, implementer_output, judge_decision, judge_feedback
    writes judge_verdict json
    writes judge_decision text
    writes judge_feedback text
    writes fix_instructions text
    on accept => done
    on implement when attempts.judge < 30 and attempts.implement < 20 => implement
    on implement => stop
    on rereview when attempts.judge < 30 => review
    on rereview => stop
    on reframe when attempts.judge < 30 and attempts.propose < 6 => propose
    on reframe => stop
    on stuck => stop
    on error => stop
    on timeout => stop

  state implement
    actor agent implementer_agent
    role implementer
    reads fix_instructions, project_dir
    writes implementer_output text
    on complete => review
    on error => stop
    on timeout => stop
