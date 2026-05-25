spec 0.1

workflow schema_smoke

  external_input query text

  max_total_steps 10

  model m_judge

  artifact verdict json
    schema "schemas/two_branch.json"
    extract feedback => judge_feedback text
  artifact judge_feedback text
    initial ""
  artifact stub_artifact text
    initial ""

  role judge_role
    prompt template "prompts/judge.md" with query

  state judge
    actor model m_judge
    role judge_role
    reads query
    writes verdict json
    writes judge_feedback text
    on accept => done
    on iterate => repeat
    on error => stop
    on timeout => stop

  state repeat
    actor model m_judge
    role judge_role
    reads query
    writes stub_artifact text
    on complete => judge
    on error => stop
    on timeout => stop
