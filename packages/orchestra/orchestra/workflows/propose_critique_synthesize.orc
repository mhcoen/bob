spec 0.1

workflow propose_critique_synthesize

  external_input instruction text
  external_input context text
  external_input prior_errors text
  external_input eliminated json
  external_input project_dir text
  external_input description text
  external_input task_label text
  external_input check_commands json
  external_input is_bug_task boolean

  max_total_steps 30

  model proposer
  model critic
  model editor

  agent editor_agent
    model editor
    adapter claude_code_agent
    context_policy fresh

  artifact proposer_output text
  artifact critic_output text
  artifact editor_output text

  state propose
    actor model proposer
    prompt template "templates/propose_critique_synthesize_proposer.md" with instruction, context, prior_errors, eliminated, description, task_label
    reads instruction, context, prior_errors, eliminated, project_dir, description, task_label, check_commands, is_bug_task
    writes proposer_output text
    on complete => critique
    on error => stop
    on timeout => stop

  state critique
    actor model critic
    prompt template "templates/propose_critique_synthesize_critic.md" with proposer_output, instruction, context, prior_errors, eliminated, description, task_label
    reads proposer_output, instruction, context, prior_errors, eliminated, project_dir, description, task_label, check_commands, is_bug_task
    writes critic_output text
    on complete => edit
    on error => stop
    on timeout => stop

  state edit
    actor agent editor_agent
    prompt template "templates/propose_critique_synthesize_editor.md" with proposer_output, critic_output, instruction, context, prior_errors, eliminated, description, task_label
    reads proposer_output, critic_output, instruction, context, prior_errors, eliminated, project_dir, description, task_label, check_commands, is_bug_task
    writes editor_output text
    on complete => done
    on error => stop
    on timeout => stop
