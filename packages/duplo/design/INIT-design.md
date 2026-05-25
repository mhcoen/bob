# `duplo init` UX design

This document specifies the command-line surface, output, and error
behavior of the `duplo init` subcommand. It is the user-facing
counterpart to `PARSER-design.md` (which specifies how SPEC.md is
read) and `DRAFTER-design.md` (which specifies how SPEC.md is
written).

`duplo init` is the new entry point for starting a project. It
replaces the implicit "first run" behavior of `duplo` (no args)
and `duplo <url>` discovering they're in a fresh directory.


## Goals

1. Make project setup explicit and separable from execution.
2. Capture intent (URL, prose, file roles) into SPEC.md before
   any expensive work happens.
3. Produce a SPEC.md the user can edit immediately, with as
   much pre-filled as inputs allow.
4. Create the `ref/` directory and its README.
5. Never block on interactive questions. The user edits SPEC.md
   in their editor, not in the terminal.


## Non-goals

1. Feature extraction, roadmap generation, or plan generation.
   Those happen on subsequent `duplo` runs against the edited
   SPEC.md.
2. Deep scraping. `duplo init` does at most a shallow scrape
   for product identity. Deep crawling is deferred to
   `duplo` (the user reviews `## Sources` first).
3. Migrating existing projects. That's `MIGRATION-design.md`.


## Command surface

```
duplo init                          # template only, no inputs
duplo init <url>                    # template pre-filled from shallow scrape
duplo init --from-description FILE  # template pre-filled from prose
duplo init --from-description -     # prose from stdin
duplo init <url> --from-description FILE   # both inputs combined
```

Flags:

- `--from-description PATH` — path to a text file (or `-` for
  stdin) containing prose describing what the user wants. The
  prose gets fed to the drafter alongside any URL scrape.
- `--deep` — opt-in to deep scraping during `init`. Default is
  shallow. Useful if the user knows they want the full crawl
  done up front and doesn't want to re-review `## Sources`.
- `--force` — overwrite an existing SPEC.md. Default behavior is
  to error if SPEC.md already exists.

No interactive prompts. No `--yes` flag needed because there's
nothing to confirm.


## Behavior by input combination

### `duplo init` (no arguments)

```
$ duplo init

Created ref/ (empty).
Created ref/README.md.
Wrote SPEC.md (template, no inputs).

Next steps:
  1. Open SPEC.md in your editor. Replace each <FILL IN> marker
     with your content. See SPEC-guide.md for details on each
     section.
  2. (Optional) Drop reference files into ref/ — screenshots,
     videos, PDFs, design mockups. Skip this if you'll provide
     a URL or rely on prose alone.
  3. (Optional) Add a URL to ## Sources in SPEC.md if you have
     a product to draw from.
  4. Run `duplo` to extract features and generate the build plan.
```

The template that gets written is the static `SPEC-template.md`
content. Every `<FILL IN>` marker is present.


### `duplo init <url>`

```
$ duplo init https://numi.app

Fetched https://numi.app (shallow scrape for product identity).
  → Identified product: Numi
  → Pre-filled ## Purpose, ## Sources

Created ref/ (empty).
Created ref/README.md.
Wrote SPEC.md.

Next steps:
  1. Open SPEC.md in your editor. Review the pre-filled sections
     and replace any remaining <FILL IN> markers (## Architecture
     is required).
  2. (Optional) Drop reference files into ref/ if you want
     specific visual direction the URL doesn't show, or behavior
     the URL doesn't capture.
  3. Run `duplo` to do the full crawl, extract features, and
     generate the build plan.

Note: duplo will deep-crawl https://numi.app on the next run.
The deep scrape is deferred so you can adjust ## Sources first
(e.g. set scrape: none, or add other URLs to crawl).
```

What gets pre-filled in SPEC.md:

- `## Purpose` — one-sentence inferred description from the
  scrape (e.g. "A macOS calculator with natural-language
  expressions and inline results.").
- `## Sources` — one entry for the URL with `role: product-reference`,
  `scrape: deep`, no proposed/discovered flag (user provided
  it explicitly).

What stays as `<FILL IN>`:

- `## Architecture` — duplo can't infer this from a scrape.
- `## Design` — left as `<FILL IN>` because we have no
  visual-target references yet. (If `--deep` was passed,
  duplo could extract design from scraped images, but for
  shallow init it doesn't.)

Other sections (`## References`, `## Scope`, `## Behavior`,
`## Notes`) are left empty (no `<FILL IN>`, just the section
header and the comment from the template).


### `duplo init --from-description description.txt`

```
$ duplo init --from-description description.txt

Read 412 chars of description from description.txt.
Drafted SPEC.md from description.

Created ref/ (empty).
Created ref/README.md.
Wrote SPEC.md.

  → Pre-filled ## Purpose, ## Design from prose.
  → ## Architecture filled from prose (description specified
    a stack); leave as <FILL IN> if not stated explicitly.
  → ## Behavior left empty (no input/output pairs detected).
  → ## Notes contains the verbatim original description.

Next steps:
  1. Open SPEC.md in your editor. Verify the drafted sections
     match your intent. Edit anything duplo got wrong.
  2. (Optional) Add a URL to ## Sources or drop files into ref/
     if you have additional reference material.
  3. Run `duplo` to extract features and generate the build plan.
```

What gets pre-filled depends on what the prose contains. The
drafter (specified in `DRAFTER-design.md`) sends the prose to
Claude with a structured-output prompt asking for each section
to be filled or left as `<FILL IN>` based on what the prose
covers.

Specifically:

- `## Purpose`, `## Design`, `## Behavior`, `## Scope`: filled
  from whatever the prose covers; left as `<FILL IN>` (or
  empty for optional sections) otherwise.
- `## Architecture`: filled ONLY when the prose explicitly
  states a language, framework, or platform. Inferring it from
  product identity ("a macOS app, so probably Swift") is not
  done — architecture is the user's choice and the LLM should
  not guess.
- `## Notes`: contains the original prose verbatim under a
  labeled header. The LLM does not invent or summarize content
  for `## Notes`.

If the prose mentions a URL (`"like numi at https://numi.app"`),
that goes into `## Sources` with `proposed: true` and a note
explaining the URL was extracted from the description.


### `duplo init <url> --from-description description.txt`

Both channels combined. Drafter merges:

- URL scrape provides product identity, base purpose, and a
  Sources entry.
- Prose adds design, behavior, scope refinements, and
  architecture (the latter only if the prose explicitly states
  a stack).
- Conflicts (e.g. prose says "dark theme" but scrape suggests
  light) resolve in favor of prose.
- Original prose preserved verbatim in `## Notes`.

Output combines both messages from above sections.


### `duplo init` against an existing SPEC.md

```
$ duplo init

Error: SPEC.md already exists in this directory.
  Use `duplo init --force` to overwrite (your existing SPEC.md
  will be lost).
  Use `duplo` to run against your existing SPEC.md.

Exit 1.
```

`--force` overwrites without further confirmation. We don't
prompt interactively; the user typed the flag, they meant it.
git makes it reversible.


### `duplo init --from-description -` (stdin)

```
$ cat description.txt | duplo init --from-description -
Read 412 chars of description from stdin.
[... same as file-based case ...]

$ duplo init --from-description -
Reading description from stdin. Press Ctrl-D when done.
[user types prose]
^D
Read 87 chars of description from stdin.
[... same as file-based case ...]
```

Stdin is the only place duplo reads interactive input, and only
because Unix piping conventions expect it. Even here, the user
edits SPEC.md in their editor afterward.


## Error cases

### Invalid URL

```
$ duplo init not-a-url

Error: 'not-a-url' is not a valid URL.
  URLs must start with http:// or https://.
  To set up without a URL, run `duplo init` (no arguments).

Exit 1.
```

### URL fetch fails

```
$ duplo init https://example-that-does-not-exist.invalid

Fetching https://example-that-does-not-exist.invalid ...
  → Failed: name resolution failed (NXDOMAIN)

duplo can still set up the project without scraping the URL.
Continuing with template-only setup.

Created ref/ (empty).
Created ref/README.md.
Wrote SPEC.md (template).

The URL has been added to ## Sources with `scrape: none` so
you can review and re-enable scraping after fixing the issue.

Exit 0.
```

Network failure during init is recoverable. Write the URL into
SPEC.md with `scrape: none` so the user knows duplo tried, and
let them edit SPEC.md to retry on the next `duplo` run.

The URL is canonicalized via `canonicalize_url` (per
PIPELINE-design.md § "URL canonicalization") before being
written to SPEC.md, regardless of fetch outcome. Without this,
a user who types `duplo init https://numi.app/` (with trailing
slash) on a failing network ends up with a Sources entry that
doesn't canonical-match what a future successful crawl
produces, leading to a duplicate append when the user retries.
The entry has no `proposed` or `discovered` flag (the user
provided the URL explicitly; fetch failure doesn't demote it
to an unreviewed inference).

### URL fetch succeeds but identifies nothing

```
$ duplo init https://example.com

Fetched https://example.com.
  → Could not identify a specific product from the page content.
  → Pre-filled ## Sources only.

Created ref/ (empty).
Created ref/README.md.
Wrote SPEC.md.

Next steps:
  1. Open SPEC.md and fill in ## Purpose, ## Architecture
     manually.
  2. Continue with normal steps below.
```

The validator (in PARSER-design.md) will catch the missing
`## Purpose` on the next `duplo` run.

### Description file not found

```
$ duplo init --from-description missing.txt

Error: file not found: missing.txt

Exit 1.
```

### Both `init` arguments invalid (URL bad AND description missing)

Errors stack: report both, exit 1, don't write anything.

### `ref/` already exists with files

```
$ duplo init https://numi.app

Fetched https://numi.app (shallow scrape for product identity).
  → Identified product: Numi
  → Pre-filled ## Purpose, ## Sources

Found existing ref/ with 3 files:
  - mockup.png
  - demo.mp4
  - notes.txt
Running Vision to propose roles ...
  → mockup.png: visual-target (proposed)
  → demo.mp4: behavioral-target (proposed)
  → notes.txt: docs (proposed)
Pre-filled ## References with 3 proposed entries.

Wrote SPEC.md.

Next steps:
  1. Open SPEC.md in your editor. Review the proposed reference
     roles. Remove `proposed: true` from each entry to confirm,
     or change the role / delete the entry if duplo got it wrong.
  2. Replace any remaining <FILL IN> markers.
  3. Run `duplo` to do the full crawl and generate the build plan.
```

Existing files in `ref/` get role proposals. Each gets
`proposed: true` so duplo won't act on them until the user
reviews. This is the same write contract specified in
PARSER-design.md.


## `ref/README.md` content

`duplo init` writes this file alongside the empty `ref/`
directory:

```markdown
# ref/

Drop reference files here that you want duplo to use as
authoritative examples of what you're building.

Accepted file types:
  - Images: png, jpg, gif, webp (UI screenshots, mockups, logos)
  - Videos: mp4, mov, webm, avi (demos, walkthroughs)
  - PDFs: spec documents, design guides, API docs
  - Text/markdown: notes, constraints, requirements

**This directory can be empty.** If SPEC.md's ## Sources section
gives duplo a URL that covers what you want, you don't need any
files here. Add files only when you want to supplement or override
what duplo can learn from the URL.

Each file you add should be listed in SPEC.md's ## References
section with a role (visual-target, behavioral-target, docs,
counter-example, ignore). When you add files and re-run duplo,
duplo will propose role entries for you to confirm or edit.

See SPEC-guide.md (in the project root) for details on each
role and when to use which.
```

Static content. Identical for every project. Written once by
`duplo init` and never modified by duplo afterward.


## Output discipline

A few rules for the output, derived from looking at the existing
duplo output style:

1. **Every line that describes an action duplo took uses
   present-tense or simple-past.** "Fetched X.", "Pre-filled Y.",
   "Created Z." Not "I have fetched..." or "Fetching X now...".
2. **Indented bullets (`  → `) describe sub-results of an
   action.** Nesting depth ≤ 2.
3. **"Next steps" sections list numbered items the user does.**
   Always present at the end of a successful `init`. Numbered
   1, 2, 3 (not bulleted) because order matters.
4. **Errors print to stderr and exit non-zero.** Successful
   informational output goes to stdout.
5. **No emoji, no color codes.** Matches existing output style;
   keeps the tool friendly to logs and CI environments.


## Interaction with subsequent `duplo` runs

After `duplo init`, the user edits SPEC.md and runs `duplo`.
The first `duplo` run after init does:

1. Read SPEC.md (parser from PARSER-design.md).
2. Run `validate_for_run` from the parser. If errors, print
   them and exit 1. The user goes back to edit SPEC.md.
3. If valid, proceed with the normal pipeline:
   - Re-scrape any sources marked `scrape: deep` or `scrape: shallow`
     (deep is the heavy work deferred from init).
   - If `## Design` has no AUTO-GENERATED block, run Vision
     (cross-image design synthesis) on `visual-target`
     references and write the autogen block. This is a
     DIFFERENT Vision call from the per-image role-proposal
     call that runs during `duplo init` (see
     DRAFTER-design.md § "Inferring file roles via Vision"
     for the role-proposal prompt; see PIPELINE-design.md
     § `design_extractor.py` for the design-synthesis prompt).
     Running both is not redundant — they produce different
     outputs (per-image roles vs. aggregated design
     requirements) and the design-synthesis call is gated
     on the autogen block being absent (write-once-never-
     replace).
   - Extract features, generate roadmap, generate Phase 1
     PLAN.md.
4. Hand off to mcloop.

`duplo init` does NOT call any of the existing `_first_run`
machinery in `main.py`. The two are entirely separate code paths.
After init, the project is in a state that the existing
"subsequent run" code can handle (with adjustments — see
`PIPELINE-design.md`).


## Implementation shape

A new `duplo/init.py` module with a single `run_init(args)`
entry point. `main.py`'s argument parser gets a new subcommand
branch alongside `fix` and `investigate`:

```python
if len(sys.argv) > 1 and sys.argv[1] in ("fix", "investigate", "init"):
    subcmd = sys.argv[1]
    if subcmd == "init":
        from duplo.init import run_init
        run_init(parse_init_args(sys.argv[2:]))
        return
    # ... existing fix/investigate handling
```

Dependencies:

- `duplo.spec_drafter` (new module, separate design doc) for
  generating the SPEC.md content.
- `duplo.fetcher.fetch_site` (existing) for the shallow scrape.
  Per PIPELINE-design.md § fetcher.py, `fetch_site` gains a
  `scrape_depth` parameter (`"deep"` | `"shallow"` | `"none"`).
  Init calls it with `scrape_depth="shallow"` to fetch only
  the entry URL with no link-following. (When `--deep` is
  passed to `duplo init`, init calls `scrape_depth="deep"`
  instead.) The `scrape_depth` enum is the single fetcher API;
  there is no separate `shallow=True` boolean.
- `duplo.validator.validate_product_url` (existing) for URL
  validation.
- `duplo.design_extractor.extract_design` (existing) is NOT
  reused for role proposal. Role proposal uses a separate
  Vision prompt specified in DRAFTER-design.md § "Inferring
  file roles via Vision" — a two-question prompt that returns
  `{description, role}` per image from a fixed enum. The
  module that owns the call is `spec_drafter.py` (same module
  as the rest of the drafter); the prompt lives alongside the
  other structured-output prompts there. `extract_design`
  remains the first-`duplo`-run Vision call (cross-image
  design synthesis producing the AUTO-GENERATED block); the
  two calls have different prompts and different outputs.
  See "Interaction with subsequent duplo runs" below for why
  running both is not wasteful.
- `duplo.scanner.scan_directory` (existing, but pointed at `ref/`
  only under the new model) for inventorying existing files.

The init path is intentionally narrow: a small orchestrator
that calls existing utilities and writes a SPEC.md via the
drafter. No new Vision prompts, no new scraping logic, no new
LLM calls outside what the drafter does.


## Open questions

1. **`mcloop.json` and `CLAUDE.md` writing.** Resolved: defer
   to first `duplo` run (the one that generates PLAN.md). Init
   produces minimum-viable state (SPEC.md + ref/); the files
   needed for mcloop integration are written when PLAN.md is.
   Note: a quick check of current code shows `CLAUDE.md` is
   written but `mcloop.json` writing wasn't located in
   `duplo/main.py` or `duplo/saver.py`; verify during
   implementation whether `mcloop.json` is in fact a duplo
   responsibility or a separate concern.

2. **URL-only vs. prose-only `<FILL IN>` asymmetry.** Resolved:
   `## Architecture` is filled ONLY when the description prose
   explicitly states a stack, platform, or language. URL-only
   mode never fills it (a URL pointing at a macOS app is not
   permission to infer Swift). Prose-mode fills it when the
   user wrote the stack down themselves; otherwise it stays
   `<FILL IN>`. Architecture is the user's choice; the LLM
   does not guess.

3. **`--deep` flag scope.** Resolved: `--deep` affects URL
   scraping only. Vision on existing `ref/` files is always
   on (proposing roles is cheap and high-value).

4. **URL fetch succeeds but identifies nothing.** Resolved:
   write what duplo can (the URL goes into `## Sources`),
   leave the rest as `<FILL IN>`, and tell the user. Forgiving
   behavior wins; the validator catches missing required
   sections on the next `duplo` run.

5. **Stdin handling.** Resolved: `--from-description -` only.
   No positional `-` shorthand; positional args are URLs and
   conflating them is confusing.
