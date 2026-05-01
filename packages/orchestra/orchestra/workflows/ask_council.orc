spec 0.1

workflow ask_council

  external_input query text
  external_input history text

  max_total_steps 60

  model m_framer
  model m_contrarian
  model m_first_principles
  model m_expansionist
  model m_outsider
  model m_executor_lens
  model m_reviewer
  model m_chairman

  artifact framed_question text
  artifact contrarian_output text
  artifact first_principles_output text
  artifact expansionist_output text
  artifact outsider_output text
  artifact executor_lens_output text
  artifact anon_map json
  artifact review_1_output text
  artifact review_2_output text
  artifact review_3_output text
  artifact review_4_output text
  artifact review_5_output text
  artifact chairman_output text

  role framer
    prompt template "templates/ask_council_framer.md" with query, history

  role contrarian
    prompt template "templates/ask_council_contrarian.md" with framed_question

  role first_principles
    prompt template "templates/ask_council_first_principles.md" with framed_question

  role expansionist
    prompt template "templates/ask_council_expansionist.md" with framed_question

  role outsider
    prompt template "templates/ask_council_outsider.md" with framed_question

  role executor_lens
    prompt template "templates/ask_council_executor_lens.md" with framed_question

  role reviewer
    prompt template "templates/ask_council_reviewer.md" with anon_map

  role chairman
    prompt template "templates/ask_council_chairman.md" with framed_question, contrarian_output, first_principles_output, expansionist_output, outsider_output, executor_lens_output, review_1_output, review_2_output, review_3_output, review_4_output, review_5_output

  # Both fan-out groups route to 'stop' on error. A failed council
  # advisor or reviewer ends the run rather than synthesizing partial
  # output: the chairman's verdict is meaningless if part of the
  # council never spoke.

  state frame
    actor model m_framer
    role framer
    reads query, history
    writes framed_question text
    on complete fan_out [contrarian_advise, first_principles_advise, expansionist_advise, outsider_advise, executor_lens_advise] join anonymize on error stop
    on error => stop
    on timeout => stop

  state contrarian_advise
    actor model m_contrarian
    role contrarian
    reads framed_question
    writes contrarian_output text
    on complete => done
    on error => stop
    on timeout => stop

  state first_principles_advise
    actor model m_first_principles
    role first_principles
    reads framed_question
    writes first_principles_output text
    on complete => done
    on error => stop
    on timeout => stop

  state expansionist_advise
    actor model m_expansionist
    role expansionist
    reads framed_question
    writes expansionist_output text
    on complete => done
    on error => stop
    on timeout => stop

  state outsider_advise
    actor model m_outsider
    role outsider
    reads framed_question
    writes outsider_output text
    on complete => done
    on error => stop
    on timeout => stop

  state executor_lens_advise
    actor model m_executor_lens
    role executor_lens
    reads framed_question
    writes executor_lens_output text
    on complete => done
    on error => stop
    on timeout => stop

  state anonymize
    actor transform anonymize_outputs
    reads contrarian_output, first_principles_output, expansionist_output, outsider_output, executor_lens_output
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
    actor model m_chairman
    role chairman
    reads framed_question, contrarian_output, first_principles_output, expansionist_output, outsider_output, executor_lens_output, review_1_output, review_2_output, review_3_output, review_4_output, review_5_output
    writes chairman_output text
    on complete => done
    on error => stop
    on timeout => stop
