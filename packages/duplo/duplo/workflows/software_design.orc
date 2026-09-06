spec 0.1

workflow software_design
  external_input query text
  external_input max_rounds int
  max_total_steps 40

  model m_author
  model m_reviewer
  model m_judge

  artifact proposal text
    initial ""
  artifact review_output text
    initial ""
  artifact verdict json
    schema "schemas/software_design_verdict.json"
    extract decision => decision text
    extract feedback => feedback text
  artifact decision text
    initial ""
  artifact feedback text
    initial ""
  artifact accepted json
    initial false
  artifact validation_feedback text
    initial ""

  role author
    prompt template "templates/software_design_author.md" with query, proposal, review_output, feedback, validation_feedback
  role reviewer
    prompt template "templates/software_design_reviewer.md" with query, proposal
  role judge_role
    prompt template "templates/software_design_judge.md" with query, proposal, review_output

  state propose
    actor model m_author
    role author
    reads query, proposal, review_output, feedback, validation_feedback
    writes proposal text
    on complete => review
    on error => stop
    on timeout => stop

  state review
    actor model m_reviewer
    role reviewer
    reads query, proposal
    writes review_output text
    on complete => judge
    on error => stop
    on timeout => stop

  state judge
    actor model m_judge
    role judge_role
    reads query, proposal, review_output
    writes verdict json
    writes decision text
    writes feedback text
    on accept => validate
    on iterate when attempts.judge < max_rounds => propose
    on iterate => done
    on stuck => stop
    on error => stop
    on timeout => stop

  state validate
    actor transform validate_software_design
    reads proposal
    writes accepted json
    writes validation_feedback text
    on complete when accepted == true => done
    on complete when attempts.judge < max_rounds => propose
    on complete => done
    on error => stop
