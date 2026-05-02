spec 0.1

workflow ask_anonymous_reviewers

  external_input query text
  external_input history text

  max_total_steps 60

  model m_framer
  model m_panelist_1
  model m_panelist_2
  model m_panelist_3
  model m_panelist_4
  model m_panelist_5
  model m_reviewer
  model m_synthesizer

  artifact framed_question text
  artifact panelist_1_output text
  artifact panelist_2_output text
  artifact panelist_3_output text
  artifact panelist_4_output text
  artifact panelist_5_output text
  artifact anon_map json
  artifact review_1_output text
  artifact review_2_output text
  artifact review_3_output text
  artifact review_4_output text
  artifact review_5_output text
  artifact synthesizer_output text

  role framer
    prompt template "templates/ask_anonymous_reviewers_framer.md" with query, history

  role panelist_1
    prompt template "templates/ask_anonymous_reviewers_panelist.md" with framed_question

  role panelist_2
    prompt template "templates/ask_anonymous_reviewers_panelist.md" with framed_question

  role panelist_3
    prompt template "templates/ask_anonymous_reviewers_panelist.md" with framed_question

  role panelist_4
    prompt template "templates/ask_anonymous_reviewers_panelist.md" with framed_question

  role panelist_5
    prompt template "templates/ask_anonymous_reviewers_panelist.md" with framed_question

  role reviewer
    prompt template "templates/ask_anonymous_reviewers_reviewer.md" with anon_map

  role synthesizer
    prompt template "templates/ask_anonymous_reviewers_synthesizer.md" with framed_question, anon_map, review_1_output, review_2_output, review_3_output, review_4_output, review_5_output

  # Both fan-out groups route to 'stop' on error. A failed panelist
  # or reviewer ends the run rather than synthesizing partial output:
  # the synthesizer's verdict is meaningless if part of the panel
  # never spoke or part of the review pass never landed.

  state frame
    actor model m_framer
    role framer
    reads query, history
    writes framed_question text
    on complete fan_out [panelist_1_state, panelist_2_state, panelist_3_state, panelist_4_state, panelist_5_state] join anonymize on error stop
    on error => stop
    on timeout => stop

  state panelist_1_state
    actor model m_panelist_1
    role panelist_1
    reads framed_question
    writes panelist_1_output text
    on complete => done
    on error => stop
    on timeout => stop

  state panelist_2_state
    actor model m_panelist_2
    role panelist_2
    reads framed_question
    writes panelist_2_output text
    on complete => done
    on error => stop
    on timeout => stop

  state panelist_3_state
    actor model m_panelist_3
    role panelist_3
    reads framed_question
    writes panelist_3_output text
    on complete => done
    on error => stop
    on timeout => stop

  state panelist_4_state
    actor model m_panelist_4
    role panelist_4
    reads framed_question
    writes panelist_4_output text
    on complete => done
    on error => stop
    on timeout => stop

  state panelist_5_state
    actor model m_panelist_5
    role panelist_5
    reads framed_question
    writes panelist_5_output text
    on complete => done
    on error => stop
    on timeout => stop

  state anonymize
    actor transform anonymize_outputs
    reads panelist_1_output, panelist_2_output, panelist_3_output, panelist_4_output, panelist_5_output
    writes anon_map json
    on complete fan_out [reviewer_1, reviewer_2, reviewer_3, reviewer_4, reviewer_5] join synthesize on error stop
    on error => stop

  state reviewer_1
    actor model m_reviewer
    role reviewer
    reads anon_map
    writes review_1_output text
    on complete => done
    on error => stop
    on timeout => stop

  state reviewer_2
    actor model m_reviewer
    role reviewer
    reads anon_map
    writes review_2_output text
    on complete => done
    on error => stop
    on timeout => stop

  state reviewer_3
    actor model m_reviewer
    role reviewer
    reads anon_map
    writes review_3_output text
    on complete => done
    on error => stop
    on timeout => stop

  state reviewer_4
    actor model m_reviewer
    role reviewer
    reads anon_map
    writes review_4_output text
    on complete => done
    on error => stop
    on timeout => stop

  state reviewer_5
    actor model m_reviewer
    role reviewer
    reads anon_map
    writes review_5_output text
    on complete => done
    on error => stop
    on timeout => stop

  state synthesize
    actor model m_synthesizer
    role synthesizer
    reads framed_question, anon_map, review_1_output, review_2_output, review_3_output, review_4_output, review_5_output
    writes synthesizer_output text
    on complete => done
    on error => stop
    on timeout => stop
