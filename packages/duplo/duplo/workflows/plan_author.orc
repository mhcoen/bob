spec 0.1

# DUPLO-OWNED, project-local fork of orchestra's iterate_until_acceptable.
#
# Resolved by Orchestra's project-local lookup (see
# orchestra.loader.lookup): when this file is deployed to a duplo-managed
# project at <project>/.orchestra/workflows/plan_author.orc, Orchestra
# resolves it ahead of any packaged workflow of the same name. This
# workflow is intentionally NOT part of Orchestra's packaged set.
#
# It extends the base proposer->reviewer->judge loop with a post-accept
# validation state so a judge's "accept" cannot stand unless the proposed
# PLAN.md body also passes canonical validation. The validation transform
# (validate_plan_body) is duplo-owned and supplied to Orchestra through the
# caller-supplied transform-registration hook on run_role/run_workflow; it
# is registered at call time, not by the packaged registry.

workflow plan_author

  external_input query text
  external_input history text
  external_input required_phase_id text
  external_input max_rounds int

  max_total_steps 60

  model m_proposer
  model m_reviewer
  model m_judge

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
  artifact validation_ok json
    initial false
  artifact validation_feedback text
    initial ""

  role proposer
    prompt template "templates/plan_author_proposer.md" with query, history, judge_decision, judge_feedback, validation_feedback

  role reviewer
    prompt template "templates/plan_author_reviewer.md" with query, proposal, judge_decision, judge_feedback

  role judge_role
    prompt template "templates/plan_author_judge.md" with query, proposal, review_output, judge_decision, judge_feedback

  state propose
    actor model m_proposer
    role proposer
    reads query, history, judge_decision, judge_feedback, validation_feedback
    writes proposal text
    on complete => review
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

  state judge
    actor model m_judge
    role judge_role
    reads query, proposal, review_output, judge_decision, judge_feedback
    writes judge_verdict json
    writes judge_decision text
    writes judge_feedback text
    on accept => validate
    on iterate when attempts.judge < max_rounds => propose
    on iterate => done
    on stuck => stop
    on error => stop
    on timeout => stop

  # Post-accept validation gate. Runs the duplo-owned validate_plan_body
  # transform on the accepted body. Cap routing mirrors the judge's
  # attempts.judge < max_rounds discipline so a body that never validates
  # terminates at 'done' with a non-accept outcome (run_role derives
  # CAPPED) instead of looping until max_total_steps (which run_role would
  # derive as ERROR). validation_feedback is fed back to the proposer so a
  # re-draft can address the canonical-validation failure.
  #
  # The transform validates the body against required_phase_id, but
  # required_phase_id is NOT a workflow read here: Orchestra forbids a
  # transform reading an external_input (transform reads must be declared
  # artifacts). Instead duplo binds required_phase_id into the transform
  # closure when it registers validate_plan_body through run_role's
  # registry_customizer hook (see T-000787/T-000789), so the only
  # workflow-level read the transform needs is the proposal body.
  state validate
    actor transform validate_plan_body
    reads proposal
    writes validation_ok json
    writes validation_feedback text
    on complete when validation_ok == true => done
    on complete when attempts.judge < max_rounds => propose
    on complete => done
    on error => stop
