spec 0.1

workflow draft_then_adjudicate

  external_input instruction text
  external_input context text
  external_input prior_errors text
  external_input eliminated json
  external_input project_dir text
  external_input description text
  external_input task_label text
  external_input task_id text
  external_input check_commands json
  external_input is_bug_task boolean
  external_input final_prompt text

  max_total_steps 30

  model m_drafter
  model m_adjudicator
  model m_editor

  agent editor_agent
    model m_editor
    adapter claude_code_agent
    context_policy fresh

  artifact drafter_output text
  artifact adjudicator_output text
  artifact editor_output text

  role drafter
    prompt template "templates/draft_then_adjudicate_drafter.md" with final_prompt

  role adjudicator
    prompt template "templates/draft_then_adjudicate_adjudicator.md" with drafter_output, final_prompt

  role editor
    prompt template "templates/draft_then_adjudicate_editor.md" with adjudicator_output, final_prompt

  state draft
    actor model m_drafter
    role drafter
    reads final_prompt
    writes drafter_output text
    on complete => adjudicate
    on error => stop
    on timeout => stop

  state adjudicate
    actor model m_adjudicator
    role adjudicator
    reads drafter_output, final_prompt
    writes adjudicator_output text
    on complete => edit
    on error => stop
    on timeout => stop

  state edit
    actor agent editor_agent
    role editor
    reads adjudicator_output, project_dir, final_prompt
    writes editor_output text
    on complete => done
    on error => stop
    on timeout => stop
