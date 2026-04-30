spec 0.1

workflow ask_single

  external_input query text
  external_input history text

  max_total_steps 5

  model m_responder

  artifact responder_output text

  role responder
    prompt template "templates/ask_single_responder.md" with history, query

  state answer
    actor model m_responder
    role responder
    reads query, history
    writes responder_output text
    on complete => done
    on error => stop
    on timeout => stop
