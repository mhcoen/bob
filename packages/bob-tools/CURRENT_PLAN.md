## Stage 20: Phase C Increment 11 - migrate duplo gap, verification, and contract appends
<!-- phase_id: phase_020 -->

- [x] T-000190: In duplo, change gap_detector.format_gap_tasks, verification_extractor.format_verification_tasks, and spec_reader.format_contracts_as_verification to return typed Task values via make_task, appended through add_phase_task. The pipeline gap and verification append sites stop writing markdown. No raw checklist markdown returned by these helpers.
- [ ] T-000191: Verify Stage 20 gate: typed tasks only; the three helpers return no markdown; output passes the canonical gate; duplo ruff and pytest green; bob-tools still green.
