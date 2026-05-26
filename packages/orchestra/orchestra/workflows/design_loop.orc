spec 0.1

workflow design_loop

  external_input query text
  external_input history text

  max_total_steps 60

  model m_judge
  model m_reviewer

  artifact proposal text
    initial ""
  artifact review_output text
    initial ""
  artifact judge_verdict json
    schema "schemas/iterate_judge_verdict.json"
    extract decision => judge_decision text
    extract feedback => judge_feedback text
  artifact judge_decision text
    initial ""
  artifact judge_feedback text
    initial ""

  role judge_role
    prompt template "templates/iterate_judge.md" with query, history, proposal, review_output, judge_decision, judge_feedback

  role reviewer
    prompt template "templates/iterate_reviewer.md" with query, proposal, judge_decision, judge_feedback

  state judge
    actor model m_judge
    role judge_role
    reads query, history, proposal, review_output, judge_decision, judge_feedback
    writes judge_verdict json
    writes judge_decision text
    writes judge_feedback text
    on accept => done
    on iterate when attempts.judge < 6 => review
    on iterate => done
    on stuck => stop
    on error => stop
    on timeout => stop

  state review
    actor model m_reviewer
    role reviewer
    reads query, proposal, judge_decision, judge_feedback
    writes review_output text
    on complete => judge
    on error => stop
    on timeout => stop
