# mcloop de-split post-completion audit — convergence note

**Reviewer**: Claude Code (Opus 4.7, 1M context).
**Date**: 2026-05-22.

Stream A: `bob/design/desplit-post-completion-audit-claudecode.md` (Claude Code; charter `bob/design/desplit-post-completion-audit.md`).
Stream B: `bob/design/desplit-post-completion-audit-kimi.md` (Kimi K2.6; charter `bob/design/desplit-post-completion-audit-kimi-charter.md`).
Audit refs: `mcloop` `desplit-complete = 901a0aff09329743c3f9a11c8667f59563cb8248`; `bob-tools` `desplit-complete = c30bdf71fe54404ad5fb7c2a8b8847b601c9adff`.

---

## Verdict

**CONVERGE WITH MECHANICAL CORRECTIONS.**

Both lineages independently report zero behavioral defects. Two mechanical corrections were surfaced — one per stream — and both are doc-side, not code-side. The corrections do not contradict each other; they are independent observations about the audit charter's wording and stage-ordering claim that did not match observed source. The behavioral state of the de-split is verified equivalent to the pre-cutover contract.

---

## Per-stream verdicts

### Stream A (Claude Code, items 1, 3, 4, 5)

- Item 1 (pre-B1 → post-D1 behavioral equivalence): 25/25 cells PASS (5 modes × 5 gates) under the corrected gate (e).
- Item 3 (audit-pattern completeness): 7 greps, 0 Bucket I hits; 2 Bucket II (filed to optional hygiene backlog); 31 Bucket III.
- Item 4 (decision-register reconciliation): 5/5 decisions PASS (D1, D2, D3, D5 active + D4 historical).
- Item 5 (deletion-surface completeness): 22/22 rows PASS (11 prod + 11 test).

Zero behavioral defects.

### Stream B (Kimi K2.6, item 2)

- (A) PASS/FAIL table: 35/35 items PASS, 0 FAIL.
- (B) Consequential findings: None found.
- (C) Design-level decisions: None required.

Zero behavioral defects.

---

## Mechanical corrections

### Correction MC-1 — gate (e) wording (surfaced by Stream A)

Stream A's pre-B1 → post-D1 capture pair flagged a single-byte divergence in BUGS.md byte equivalence on modes where the `## Bugs` section was empty (or became empty post-run). Root cause: planfile's renderer (`bob_tools/planfile/renderer.py:71-74` per Stream B's independent re-derivation) strips trailing blank lines on an empty section. `## Bugs\n\n` → `## Bugs\n`. This is a renderer normalization, the same class of canonicalization the charter §1.1 already excluded from PLAN.md byte equivalence; the charter omitted it from the BUGS.md case in error.

Stream A's resolution: gate (e) restated as "byte-identical modulo planfile renderer normalization" — operationally `render_plan(parse_plan(pre)) == post`. Under the corrected gate, all 25/25 PASS.

Stream B's independent observation: Stream B's item 19 cited exactly the same renderer normalization (`renderer.py:71-74`, the `while lines and not lines[-1]: lines.pop()` loop) and classified it `PASS (normalization property)` without prompting. Independent convergence on the underlying observation; corroborates Stream A's charter-side correction.

### Correction MC-2 — stage-ordering re-derivation (surfaced by Stream B)

Stream B's item 29 reports the actual landed stage order from `git log` as `B0.2 → B2 → B3 → B1+B3 → D3 → D2 → D1a → D1`. The Kimi charter §1.13 had stated the forced ordering as `B0 → B1+B3 → B2 → B4 → B5 → D3 → D2 → D1a → D1`, which Stream B re-derived and corrected: B2 in fact preceded B1+B3 rather than following it. All load-bearing dependencies (B0.1 before B0.2, B1+B3 atomic, D3 before D2 before D1a before D1) are preserved by the actual landed order. Cross-checked here against `git log` independently: B2 commit `0c4d6b7` (route phase-id resolution through planfile context shim) lands before B1+B3 commit `eb80d13` (cutover) — Stream B's claim is correct.

This is a documentation correction to the charter's stated ordering, not a code defect. The charter listed an idealized planning order that the actual implementation didn't strictly follow; the implementation took a valid ordering that the charter's stated ordering didn't capture.

Stream B also notes a citation-path correction in its item 25: the B3 harness hermeticity test moved from `tests/test_stub_run.py` to `tests/integration/test_stub_run.py`. Mechanical citation drift, no semantic change.

---

## Items audited by both streams (shared coverage)

Where both streams independently audited the same decision register entries:

| Decision | Stream A verdict (item 4.x) | Stream B verdict (item 30-33) | Convergence |
|---|---|---|---|
| D1 — drop `work_observed` | PASS | PASS | converges |
| D2 — collapse ordinal to (`"none"`, `None`) | PASS | PASS | converges |
| D3 — `--retry` through `clear_failed` on both files | PASS | PASS | converges |
| D5 — `purge_done_bug_tasks` filters DONE | PASS | PASS | converges |

Citation-line drift between streams: both cite `main.py:854-855` for D3, both cite `ledger_emit.py:152-171` for D2's no-ordinal collapse (Stream A: `:152-168` + `:171`; Stream B: `:153-171`) — within mechanical drift tolerance, the same code paths.

Independent file:line corroboration on the renderer-normalization property: Stream A cited the runtime behavioral signal (`bug_only` mode BUGS.md output) and proposed the gate-(e) charter correction; Stream B cited the planfile source (`renderer.py:71-74`) and classified it as a normalization property in §2(g). Same finding, independent derivations.

---

## Items audited by Stream B only (Stream A's charter did not include)

- Stage-ordering re-derivation (Stream B item 29 → MC-2 above). Stream A's charter scoped items 1, 3, 4, 5 only; §3 forced ordering was Stream B's scope.
- §2(d) ID-prefix strip via `_extract_task_id` (Stream B item 9). Stream A confirmed task-id mutation contract under item 4.5 / item 5; Stream B re-derived the parser's strip step independently.
- Several §2 parity items at finer granularity (Stream B items 1, 4, 5, 6, 7, 8 — the parser/walker/batch/classifier parity). Stream A treated these implicitly under the end-to-end equivalence captures.

No divergence: Stream B's broader §2 coverage corroborates Stream A's end-to-end capture without contradicting it. Both streams independently arrive at "zero behavioral defects".

---

## Items audited by Stream A only (Stream B's charter did not include)

- Pre-B1 → post-D1 stub-backed capture sweep with five gates × five modes (Stream A item 1). Stream B's §2 re-derivation is static; Stream A's is dynamic. Both are valid lineages and reach the same conclusion.
- Deletion-surface row-table audit at §2(h) granularity (Stream A item 5). Stream B's items 23, 26, 27, 28 cover a subset.

No divergence: complementary coverage, no contradicting findings.

---

## Closure criterion (per May 17 precedent + this charter §5)

Both lineages independently reported zero behavioral defects. Two mechanical doc corrections were surfaced (gate (e) wording; stage-ordering re-derivation), both already recorded in the respective stream reports and herein. No divergence on any verdict; no item where one stream reports a finding the other contradicts.

**The audit converges.** Consolidation planning may proceed from the verified base. The integration plan's closure paragraph is appended as a separate scoped commit per the charter §8.
