spec 0.1

workflow council_four_canonical

  external_input state text
  external_input question text
  external_input ledger_slice text
  external_input design_context text
  external_input required_phase_id text

  max_total_steps 30

  model m_framer
  model m_proposer_code
  model m_proposer_codex
  model m_proposer_kimi
  model m_proposer_deepseek
  model m_synthesizer

  artifact council_brief text
  artifact proposal_code text
  artifact proposal_codex text
  artifact proposal_kimi text
  artifact proposal_deepseek text
  artifact plan text
  artifact judge_verdict json
    schema "schemas/council_synthesis_verdict_canonical.json"
    extract decision => judge_decision text
    extract feedback => judge_feedback text
  artifact judge_decision text
    initial ""
  artifact judge_feedback text
    initial ""

  role framer
    prompt template "templates/council_framer_canonical.md" with state, question, ledger_slice, design_context, required_phase_id

  role proposer_code
    prompt template "templates/council_proposer.md" with council_brief

  role proposer_codex
    prompt template "templates/council_proposer.md" with council_brief

  role proposer_kimi
    prompt template "templates/council_proposer.md" with council_brief

  role proposer_deepseek
    prompt template "templates/council_proposer.md" with council_brief

  role synthesizer
    prompt template "templates/council_synthesizer_canonical.md" with council_brief, proposal_code, proposal_codex, proposal_kimi, proposal_deepseek

  state frame
    actor model m_framer
    role framer
    reads state, question, ledger_slice, design_context, required_phase_id
    writes council_brief text
    on complete fan_out [propose_code, propose_codex, propose_kimi, propose_deepseek] join synthesize on error stop
    on error => stop
    on timeout => stop

  state propose_code
    actor model m_proposer_code
    role proposer_code
    reads council_brief
    writes proposal_code text
    on complete => done
    on error => stop
    on timeout => stop

  state propose_codex
    actor model m_proposer_codex
    role proposer_codex
    reads council_brief
    writes proposal_codex text
    on complete => done
    on error => stop
    on timeout => stop

  state propose_kimi
    actor model m_proposer_kimi
    role proposer_kimi
    reads council_brief
    writes proposal_kimi text
    on complete => done
    on error => stop
    on timeout => stop

  state propose_deepseek
    actor model m_proposer_deepseek
    role proposer_deepseek
    reads council_brief
    writes proposal_deepseek text
    on complete => done
    on error => stop
    on timeout => stop

  state synthesize
    actor model m_synthesizer
    role synthesizer
    reads council_brief, proposal_code, proposal_codex, proposal_kimi, proposal_deepseek
    writes judge_verdict json
    writes judge_decision text
    writes judge_feedback text
    writes plan text
    on accept => done
    on reframe => stop
    on stuck => stop
    on error => stop
    on timeout => stop
