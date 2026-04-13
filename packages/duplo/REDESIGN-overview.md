# Redesign overview

This is the entry point for the SPEC.md / `ref/` / `duplo init`
redesign. Read this first; it points to the detail docs.

## What we're changing

The current duplo input model is under-specified: files in the
project root get heuristically classified, URLs get crawled from
arbitrary text files, intent lives partly in interactive prompts
and partly in `SPEC.md` (which is optional), and pipeline stages
infer roles from file properties.

The new model:

1. **`SPEC.md` is the contract.** It declares product purpose,
   architecture, design intent, scope, behavioral contracts,
   sources (URLs), and references (files in `ref/`). Every
   downstream pipeline stage reads role-filtered input from
   the parsed spec.

2. **`ref/` holds reference files.** All user-provided
   reference materials (screenshots, videos, PDFs, docs)
   live in `ref/`. Files in the project root that aren't
   declared duplo artifacts are no longer scanned.

3. **`duplo init` sets up the project.** It drafts SPEC.md
   from inputs (URL, prose, existing files), creates `ref/`,
   and stops. The user edits SPEC.md in their editor before
   running `duplo`.

4. **`duplo` executes against SPEC.md.** No more interactive
   first-run questions; SPEC.md is the answer to those
   questions.

5. **Inference is visible.** When duplo proposes a URL or a
   file role, it writes the proposal into SPEC.md with a
   `proposed: true` flag and refuses to act on the proposal
   until the user removes the flag. Inference becomes a
   reviewable artifact instead of hidden behavior.


## Documents in this redesign

In suggested reading order:

1. **`SPEC-template.md`** — the actual SPEC.md template
   `duplo init` will copy from. Terse, form-like, dominated
   by section headers and `<FILL IN>` markers.

2. **`SPEC-guide.md`** — depth on each SPEC.md section: what
   it's for, what fields it accepts, examples, the edit
   contract, the relationship between SPEC.md and
   `.duplo/duplo.json`. The user-facing reference doc.

3. **`PARSER-design.md`** — how `spec_reader.py` evolves to
   parse the new schema. Dataclasses, validation rules,
   per-stage formatters, migration path from the existing
   parser, test plan, open questions.

4. **`DRAFTER-design.md`** — the new `spec_drafter.py`
   module that writes SPEC.md. Drafting from inputs,
   appending proposed/discovered entries, AUTO-GENERATED
   block management, edit safety.

5. **`INIT-design.md`** — the new `duplo init` subcommand.
   Command surface, output for each input combination,
   error handling, the `ref/README.md` content.

6. **`MIGRATION-design.md`** — minimal manual migration for
   existing projects. Detection plus a printed instructions
   message. No auto-move, no auto-generated SPEC.md.

7. **`PIPELINE-design.md`** — how existing pipeline stages
   (scanner, fetcher, design extractor, video extractor,
   investigator, etc.) consume role-filtered input from
   the new parser. The biggest implementation phase.


## Key design decisions made

These came out of the conversation that produced these docs:

1. **Command name is `duplo init`** (not `duplo setup`).
2. **Migration is manual and minimal**. Only a handful of
   projects use duplo; on the old layout, `duplo` prints
   instructions and exits. The user moves files into `ref/`
   and runs `duplo init` themselves. No auto-move, no
   auto-generated SPEC.md.
3. **`<FILL IN>` markers**, not "TODO" — keeps SPEC.md's
   vocabulary distinct from PLAN.md's task vocabulary.
4. **No interactive prompts**. Every input channel can be
   provided non-interactively (URL on command line, prose
   via `--from-description`, files dropped in `ref/`). The
   user edits SPEC.md in their editor.
5. **Three input channels**: URL (in `## Sources`), files
   (in `ref/`, declared in `## References`), prose (in
   `## Purpose`, `## Architecture`, `## Design`,
   `## Behavior`, `## Notes`). Any one is sufficient. More
   is better.
6. **Template-first, not Q&A-first**. duplo writes a
   template; user edits it.
7. **Inference is visible**. `proposed: true` and
   `discovered: true` flags on entries duplo added; user
   reviews and removes flag to confirm.
8. **AUTO-GENERATED blocks in `## Design`** for Vision-extracted
   design details. Write-once-never-replace: the user deletes
   the block to regenerate. Avoids hash-based edit detection
   and the failure modes that come with it.
9. **No "TODO" anywhere in SPEC.md**, the template, setup
   output, error messages, or `ref/README.md`.
10. **Web search is not added to the pipeline.** Inference
    is fine; ambient web search during extraction or
    planning is not. The one narrow exception is during
    `duplo init`, where a URL extracted from prose can be
    proposed to `## Sources` with `proposed: true`.
11. **No raw SPEC.md text in LLM prompts.** Critical safety
    invariant: `format_spec_for_prompt` serializes from the
    parsed dataclasses with role/flag filtering, never from
    `spec.raw`. Without this, `proposed:`, `discovered:`, and
    `counter-example` entries would leak into every LLM call
    despite the role-filter helpers. The invariant is scoped
    to SPEC.md content; scraped HTML from product-reference
    sources reaches the LLM unfiltered, matching the existing
    trust model. See PIPELINE-design.md § "Prompt-injection
    invariant: scope" for the full discussion.

12. **Same-origin deep crawl, cross-origin discovered-only.**
    A `scrape: deep` source crawls and uses content from
    same-origin links in the current run; cross-origin links
    are recorded as `discovered: true` and NOT fetched until
    the user reviews. Resolves the contradiction where today's
    deep crawl uses link content immediately while still
    flagging links for review.

13. **Multiple roles per reference entry.** A demo video can
    declare `role: behavioral-target, visual-target` and
    contribute to both verification and design extraction.
    Preserves the dual-use case from today's pipeline.

14. **`## Architecture` parsed into `BuildPreferences` via
    LLM.** The free-form prose gets a small structured-output
    LLM call to populate the existing `BuildPreferences`
    dataclass. Cached in `duplo.json`; re-parsed when prose
    changes.


## Implementation phasing

The redesign breaks into five phases, ordered so each phase
leaves duplo in a runnable state and each new piece is
supported by everything underneath it.

**Phase 1: Parser with backward compatibility.** Add
`SourceEntry`, `ReferenceEntry`, `DesignBlock` dataclasses;
add `## Sources` and `## Notes` parsing; convert
`## References` from prose to structured entries; rewrite
`format_spec_for_prompt` to serialize from dataclasses
(critical safety invariant); add per-stage formatters; add
`validate_for_run`. Preserve existing field-level API where
possible; provide compatibility helpers for callers that
accessed `spec.references` and `spec.design` as strings.
End state: parser handles new schema and old schema; existing
callers continue to work via the compatibility layer; safety
invariant is in place before any code reads from the parser.
**Status: shipped.**

**Phase 2: Migration detection (small, ships before pipeline).**
Add the `needs_migration` check and the printed instructions
message that runs at the start of `main()` for the no-subcommand
path. Pre-redesign projects (no SPEC.md, no `ref/`, but
`.duplo/duplo.json` exists) get redirected before they hit any
new code paths. The instructions message at this stage reads
"manually create `ref/`, move your reference files into it,
then author a SPEC.md by hand using SPEC-template.md as a
guide" — `duplo init` doesn't yet exist. Phase 4 will upgrade
the message to reference `duplo init` once it ships. End state:
existing projects get a clear instructions-and-exit message
instead of running into pipeline failures; new projects (with
a hand-authored SPEC.md and `ref/`) proceed to the existing
`_subsequent_run` path unchanged.

The ordering rationale: pipeline integration in Phase 3
restructures `_subsequent_run` to require SPEC.md. Without
migration detection landing first, an existing project running
`duplo` at the Phase 3 boundary would hit the new SPEC.md-
requiring code path with no SPEC.md and no actionable error.
Migration detection is small enough (a single check + a printed
message) that landing it ahead of pipeline integration costs
little and prevents a real broken-state window.

**Phase 3: Pipeline integration.** Wire the parser into the
existing pipeline orchestration: route role-filtered inputs
to design extraction, video pipeline, PDF/text extraction,
feature extraction. Add `BuildPreferences` parsing from
`## Architecture`. Add `app_name` derivation. Add same-origin
restriction to `fetch_site` deep mode. Add multi-source
persistence to `duplo.json`. Implement the minimal subset of
`spec_drafter.py` needed for write-backs (`append_sources`
for discovered URLs, `update_design_autogen` for design
write-back) — the rest of the drafter (drafting from inputs,
the LLM call) waits for Phase 4. End state: a project with a
manually-authored SPEC.md (in the new format) in a fresh
`ref/` directory works end-to-end through the existing
`_subsequent_run` path. `_first_run` is unchanged.

**Phase 4: Drafter and `duplo init`.** Implement the rest
of `spec_drafter.py` (drafting from inputs via LLM, the
remaining append/format helpers). Implement `duplo init` as
a new subcommand that uses the drafter. Update the migration
message from Phase 2 to reference `duplo init` (one-line
change). End state: `duplo init` creates new projects in the
new format; `duplo` runs against them via the Phase 3
pipeline integration; the migration path is now fully
self-service.

**Phase 5: Cleanup and full pipeline transition.** Remove
the old `_first_run` path. Remove URL-in-text-file scanning.
Remove file-relevance heuristics in `scanner`. Remove the
compatibility layer added in Phase 1 once all callers have
been updated. Update investigator to include counter-examples
and behavior contracts. Tighten gap detection around
`scope_exclude`. End state: only the new model is supported;
code is clean.

This ordering is risk-managed: each phase is shippable on
its own (`duplo` works at every phase boundary), and no
phase ships a feature that depends on a not-yet-implemented
piece. Phase 5's removals are deliberately last so the new
code has time to prove itself before old paths are deleted.

Estimated relative size: Phase 3 is the largest (most
pipeline modules touched). Phase 5 is the second-largest
(removals require careful caller audits). Phases 2 and 4
are smaller and more focused.


## What stays the same

Worth listing what isn't changing, to scope the work:

- PLAN.md format and discipline. Plans still have phase
  headings, `[feat:]` and `[fix:]` annotations, `[BATCH]`
  parents, `## Bugs` section.
- mcloop integration. Mcloop reads PLAN.md the same way.
- Phase completion logic. Annotations still drive feature
  status updates.
- The roadmap → phase plan workflow.
- All `duplo.json` fields that aren't superseded by SPEC.md
  (features, status, phases, roadmap, current_phase, issues,
  reference_urls).
- The `duplo fix` and `duplo investigate` subcommands. They
  gain new context sources (counter-examples, behavior
  contracts) but their command surface is unchanged.
- The McLoop-wrap crash handler at the top of `main.py`.
- All non-pipeline modules: `notifier`, `comparator`,
  `issuer`, `appshot`, `selector` (used in subsequent runs),
  etc.


## Open questions across all docs

All major open questions have been resolved (per Codex
review feedback) and the resolutions are documented in the
relevant detail docs. Summary:

1. **`.duplo/product.json`**: keep as a cache. Product
   identity in `## Purpose` is prose (not a stable key); the
   JSON cache provides a stable identifier and the home for
   `app_name`. Possible Phase 2 cleanup: fold into
   `duplo.json`.

2. **Strict mode**: not added. The pipeline always ignores
   unreviewed entries; warnings are sufficient.

3. **`## Notes` from drafting**: the LLM does NOT write
   `## Notes`. When `--from-description` is used, the
   original prose is copied verbatim under a labeled header.

4. **Two-pass scrape**: wait. Cross-origin discovered URLs
   are recorded as `discovered: true` and not scraped until
   the user reviews. Same-origin links during deep crawl are
   scraped immediately.

5. **`mcloop.json` and `CLAUDE.md`**: deferred to first
   `duplo` run, not written by `duplo init`. Verify during
   implementation whether `mcloop.json` is in fact a duplo
   responsibility (initial code search did not find it being
   written).

Remaining open items — not blocking, but worth confirming
before implementation:

- **Implementation order within each phase.** The detail
  docs propose orderings but final task breakdown for the
  PLAN.md is a separate decision when the phase is ready to
  run.
- **Test infrastructure for property-based round-trip
  testing.** The drafter's round-trip property test relies
  on generating arbitrary `ProductSpec` instances. Worth
  deciding whether to use Hypothesis (added dependency) or
  hand-rolled fixture generation (more code but no new
  dep).


## Suggested next steps

In order of priority:

1. **Read the docs in the order listed above.** SPEC-template
   and SPEC-guide first to confirm the user-facing model.
   PARSER and PIPELINE for the data shapes and behavior.
   INIT and DRAFTER for how new projects get bootstrapped.
   MIGRATION last (it's a 50-line doc).

2. **Confirm the resolved open questions.** They're documented
   above and in each detail doc; if any resolution looks wrong
   on closer reading, raise it before implementation.

3. **Decide on phase ordering or recombination.** The five
   phases above are independent enough that some could
   combine (e.g. Phase 1 parser + Phase 2 minimal pipeline
   support if the type-API churn is small) or split
   (Phase 5 cleanup is the largest after Phase 2 and may
   warrant sub-phases). Worth thinking about how mcloop can
   deliver useful intermediate state.

4. **Pick a phase to start.** Phase 1 (parser) is shipped.
   Phase 2 (migration detection) is the next thing to land
   — small (a check + a message) and required before the
   pipeline restructure in Phase 3 can ship without breaking
   existing projects.

5. **For the chosen phase, write a real PLAN.md.** Each task
   gets concrete deliverables and test subtasks. Apply the
   lesson from the bug-fix round: every distinct deliverable
   gets its own subtask checkbox; parent tasks don't carry
   work the subtasks don't cover.

6. **Run mcloop on the PLAN.md.** Audit the result the way
   we did for the bugs. Iterate.

7. **Move to the next phase.** Repeat.


## A note on scope

This redesign touches a lot of duplo's internals but doesn't
change what duplo *is*. It still scrapes a product, extracts
features, plans phases, and hands off to mcloop. The redesign
makes the input contract explicit, makes inference visible,
and makes the user's intent the source of truth. Everything
else carries forward.

If at any point during implementation it feels like the
scope is creeping (e.g. "while we're refactoring, let's also
change how features are tracked" or "let's add new pipeline
stages"), push back. The redesign's success criterion is
"existing projects work the same way after migration, and
new projects have a clearer, more predictable input model."
Not "duplo also does new things."
