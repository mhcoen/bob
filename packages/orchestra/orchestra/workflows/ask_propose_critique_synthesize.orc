spec 0.1

workflow ask_propose_critique_synthesize

  external_input query text
  external_input history text

  max_total_steps 20

  model m_proposer
  model m_critic
  model m_synthesizer
  model m_responder

  artifact proposer_output text
  artifact critic_output text
  artifact synthesizer_output text
  artifact responder_output text

  role proposer
    prompt template "templates/ask_propose_critique_synthesize_proposer.md" with history, query

  role critic
    prompt template "templates/ask_propose_critique_synthesize_critic.md" with history, proposer_output, query

  role synthesizer
    prompt template "templates/ask_propose_critique_synthesize_synthesizer.md" with history, proposer_output, critic_output, query

  role responder
    prompt template "templates/ask_propose_critique_synthesize_responder.md" with history, synthesizer_output, query

  state propose
    actor model m_proposer
    role proposer
    reads query, history
    writes proposer_output text
    on complete => critique
    on error => stop
    on timeout => stop

  state critique
    actor model m_critic
    role critic
    reads proposer_output, query, history
    writes critic_output text
    on complete => synthesize
    on error => stop
    on timeout => stop

  state synthesize
    actor model m_synthesizer
    role synthesizer
    reads proposer_output, critic_output, query, history
    writes synthesizer_output text
    on complete => answer
    on error => stop
    on timeout => stop

  state answer
    actor model m_responder
    role responder
    reads synthesizer_output, query, history
    writes responder_output text
    on complete => done
    on error => stop
    on timeout => stop
