spec 0.1

workflow ask_draft_then_adjudicate

  external_input query text
  external_input history text

  max_total_steps 15

  model m_drafter
  model m_adjudicator
  model m_responder

  artifact drafter_output text
  artifact adjudicator_output text
  artifact responder_output text

  role drafter
    prompt template "templates/ask_draft_then_adjudicate_drafter.md" with history, query

  role adjudicator
    prompt template "templates/ask_draft_then_adjudicate_adjudicator.md" with history, drafter_output, query

  role responder
    prompt template "templates/ask_draft_then_adjudicate_responder.md" with history, adjudicator_output, query

  state draft
    actor model m_drafter
    role drafter
    reads query, history
    writes drafter_output text
    on complete => adjudicate
    on error => stop
    on timeout => stop

  state adjudicate
    actor model m_adjudicator
    role adjudicator
    reads drafter_output, query, history
    writes adjudicator_output text
    on complete => answer
    on error => stop
    on timeout => stop

  state answer
    actor model m_responder
    role responder
    reads adjudicator_output, query, history
    writes responder_output text
    on complete => done
    on error => stop
    on timeout => stop
