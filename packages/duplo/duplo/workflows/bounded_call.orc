spec 0.1

workflow bounded_call
  external_input query text
  max_total_steps 1
  model m_worker
  artifact response text
  role worker
    prompt template "templates/bounded_call.md" with query
  state call
    actor model m_worker
    role worker
    reads query
    writes response text
    on complete => done
    on error => stop
    on timeout => stop
