spec 0.1

workflow ask_draft_then_adjudicate

  external_input query text

  max_total_steps 15

  model m_drafter
  model m_adjudicator
  model m_editor

  artifact drafter_output text
  artifact adjudicator_output text
  artifact editor_output text

  role drafter
    prompt template "templates/ask_draft_then_adjudicate_drafter.md" with query

  role adjudicator
    prompt template "templates/ask_draft_then_adjudicate_adjudicator.md" with drafter_output, query

  role editor
    prompt template "templates/ask_draft_then_adjudicate_editor.md" with adjudicator_output, query

  state draft
    actor model m_drafter
    role drafter
    reads query
    writes drafter_output text
    on complete => adjudicate
    on error => stop
    on timeout => stop

  state adjudicate
    actor model m_adjudicator
    role adjudicator
    reads drafter_output, query
    writes adjudicator_output text
    on complete => answer
    on error => stop
    on timeout => stop

  state answer
    actor model m_editor
    role editor
    reads adjudicator_output, query
    writes editor_output text
    on complete => done
    on error => stop
    on timeout => stop
