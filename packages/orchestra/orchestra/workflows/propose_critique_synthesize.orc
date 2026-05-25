spec 0.1

workflow propose_critique_synthesize

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

  max_total_steps 40

  model m_proposer
  model m_critic
  model m_synthesizer
  model m_editor

  agent editor_agent
    model m_editor
    adapter claude_code_agent
    context_policy fresh

  artifact proposer_output text
  artifact critic_output text
  artifact synthesizer_output text
  artifact editor_output text

  role proposer
    prompt template "templates/propose_critique_synthesize_proposer.md" with final_prompt

  role critic
    prompt template "templates/propose_critique_synthesize_critic.md" with proposer_output, final_prompt

  role synthesizer
    prompt template "templates/propose_critique_synthesize_synthesizer.md" with proposer_output, critic_output, final_prompt

  role editor
    prompt template "templates/propose_critique_synthesize_editor.md" with synthesizer_output, final_prompt

  state propose
    actor model m_proposer
    role proposer
    reads final_prompt
    writes proposer_output text
    on complete => critique
    on error => stop
    on timeout => stop

  state critique
    actor model m_critic
    role critic
    reads proposer_output, final_prompt
    writes critic_output text
    on complete => synthesize
    on error => stop
    on timeout => stop

  state synthesize
    actor model m_synthesizer
    role synthesizer
    reads proposer_output, critic_output, final_prompt
    writes synthesizer_output text
    on complete => edit
    on error => stop
    on timeout => stop

  state edit
    actor agent editor_agent
    role editor
    reads synthesizer_output, project_dir, final_prompt
    writes editor_output text
    on complete => done
    on error => stop
    on timeout => stop
