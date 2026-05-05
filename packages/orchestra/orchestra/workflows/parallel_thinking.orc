spec 0.1

workflow parallel_thinking

  external_input query text
  external_input history text

  max_total_steps 30

  model m_framer
  model m_panelist_1
  model m_panelist_2
  model m_panelist_3
  model m_panelist_4
  model m_panelist_5

  artifact framed_question text
  artifact panelist_1_output text
  artifact panelist_2_output text
  artifact panelist_3_output text
  artifact panelist_4_output text
  artifact panelist_5_output text
  artifact finish_marker text

  role framer
    prompt template "templates/parallel_thinking_framer.md" with query, history

  role panelist_1
    prompt template "templates/parallel_thinking_panelist.md" with framed_question

  role panelist_2
    prompt template "templates/parallel_thinking_panelist.md" with framed_question

  role panelist_3
    prompt template "templates/parallel_thinking_panelist.md" with framed_question

  role panelist_4
    prompt template "templates/parallel_thinking_panelist.md" with framed_question

  role panelist_5
    prompt template "templates/parallel_thinking_panelist.md" with framed_question

  state frame
    actor model m_framer
    role framer
    reads query, history
    writes framed_question text
    on complete fan_out [p1, p2, p3, p4, p5] join finish on error stop
    on error => stop
    on timeout => stop

  state p1
    actor model m_panelist_1
    role panelist_1
    reads framed_question
    writes panelist_1_output text
    on complete => done
    on error => stop
    on timeout => stop

  state p2
    actor model m_panelist_2
    role panelist_2
    reads framed_question
    writes panelist_2_output text
    on complete => done
    on error => stop
    on timeout => stop

  state p3
    actor model m_panelist_3
    role panelist_3
    reads framed_question
    writes panelist_3_output text
    on complete => done
    on error => stop
    on timeout => stop

  state p4
    actor model m_panelist_4
    role panelist_4
    reads framed_question
    writes panelist_4_output text
    on complete => done
    on error => stop
    on timeout => stop

  state p5
    actor model m_panelist_5
    role panelist_5
    reads framed_question
    writes panelist_5_output text
    on complete => done
    on error => stop
    on timeout => stop

  state finish
    actor transform finish_panel
    reads panelist_1_output, panelist_2_output, panelist_3_output, panelist_4_output, panelist_5_output
    writes finish_marker text
    on complete => done
    on error => stop
