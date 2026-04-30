spec 0.1

workflow ask_single

  external_input query text

  max_total_steps 5

  model m_editor

  artifact editor_output text

  role editor
    prompt template "templates/ask_single_editor.md" with query

  state answer
    actor model m_editor
    role editor
    reads query
    writes editor_output text
    on complete => done
    on error => stop
    on timeout => stop
