spec 0.1

workflow single

  external_input instruction text
  external_input context text
  external_input prior_errors text
  external_input eliminated json
  external_input project_dir text
  external_input description text
  external_input task_label text
  external_input check_commands json
  external_input is_bug_task boolean

  max_total_steps 10

  model editor

  agent editor_agent
    model editor
    adapter claude_code_agent
    context_policy fresh

  artifact editor_output text

  state edit
    actor agent editor_agent
    prompt template "templates/single_editor.md" with instruction, context, prior_errors, eliminated, description, task_label
    reads instruction, context, prior_errors, eliminated, project_dir, description, task_label, check_commands, is_bug_task
    writes editor_output text
    on complete => done
    on error => stop
    on timeout => stop
