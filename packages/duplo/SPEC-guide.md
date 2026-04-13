# SPEC.md guide

This document explains each section of `SPEC.md` in detail. The
template itself is intentionally terse; the depth lives here.

If you've never written a SPEC.md before, skim "How it fits
together" and "The minimum viable spec" below, then look up
specific sections as you need them.


## How it fits together

```
SPEC.md  →  duplo  →  PLAN.md  →  mcloop  →  code
```

- **SPEC.md** is your specification. You author it.
- **duplo** reads SPEC.md and produces PLAN.md (a task list).
- **PLAN.md** is the input to mcloop. mcloop never reads SPEC.md.
- **mcloop** executes the tasks in PLAN.md to produce code.

Editing SPEC.md changes what duplo builds on the next run.
Editing PLAN.md changes what mcloop does on the next run.
The two files have different audiences and different conventions.

`duplo init` drafts SPEC.md for you. Subsequent `duplo` runs may
append to `## Sources` and `## References` when new URLs or files
are discovered, but never modify your other sections. Appended
entries are flagged (`proposed: true` or `discovered: true`) and
duplo will not act on them until you remove the flag.


## The minimum viable spec

The smallest SPEC.md duplo will run against needs:

- `## Purpose` filled in.
- `## Architecture` filled in.
- At least one of: a URL in `## Sources`, a file in `## References`,
  or enough detail in `## Purpose` and `## Architecture` for duplo
  to plan without external references.

Everything else is optional.


## Section reference

### `## Purpose`

One or two sentences describing what you're building. This is
injected into every LLM call duplo makes, so it sets the framing
for feature extraction, roadmap generation, and plan generation.

Required. Leave the `<FILL IN>` marker in place if you don't
have an answer yet — duplo will refuse to run.

Examples:

    A macOS menu bar calculator like numi — natural-language
    expressions with inline results, dark theme.

    A CLI that converts Markdown files to a flat JSON AST, with
    support for GFM tables and footnotes.

    A FastAPI service exposing a REST interface to a SQLite
    database of book metadata, with full-text search.


### `## Sources`

URLs duplo should treat as authoritative references. One entry
per URL.

Each entry has:

- A URL on its own line (prefixed with `- `).
- `role:` — one of:
    - `product-reference` — the product you're duplicating or
      drawing from. duplo extracts features, design, and behavior
      from these.
    - `docs` — documentation duplo should crawl for behavior. Used
      for behavioral grounding but not for product identity.
    - `counter-example` — NOT what you're building. Used for
      contrast in investigation but never for feature extraction.
- `scrape:` — one of:
    - `deep` — crawl the URL and follow documentation links.
    - `shallow` — fetch only the URL itself, no link-following.
    - `none` — don't fetch; the URL is declarative context only.
- `notes:` — optional free-form prose.

Optional section. If `## References` or `## Purpose` provide
enough information on their own, you can leave `## Sources`
blank.

Example:

    - https://numi.app
      role: product-reference
      scrape: deep
      notes: Authoritative for feature set and expression syntax.

    - https://github.com/nikolaeu/numi/wiki
      role: docs
      scrape: deep
      notes: Behavioral ground truth.

    - https://soulver.app
      role: counter-example
      scrape: none
      notes: Different product. Don't extract features from this.

#### Discovered URLs

When duplo crawls a URL with `scrape: deep`, it follows links to
documentation pages. Discovered URLs get appended to `## Sources`
with `discovered: true`:

    - https://numi.app/docs/syntax
      role: docs
      scrape: deep
      discovered: true
      notes: Discovered via link from https://numi.app on 2026-04-12.

duplo will not crawl `discovered: true` URLs on subsequent runs
until you remove the flag, confirming you reviewed them.


### `## References`

Files in `ref/` that duplo should treat as authoritative. One
entry per file.

Each entry has:

- A path on its own line (prefixed with `- `, e.g. `ref/main.png`).
  Filenames with spaces work bare; if you have unusual characters
  in the name, wrap the path in double quotes
  (`- "ref/odd:file.png"`).
- `role:` — one or more of the following, comma-separated:
    - `visual-target` — shows what you want the UI to look like.
      Sent to Vision for design extraction.
    - `behavioral-target` — shows how the app should behave (e.g.
      a demo video). Sent to frame extraction and verification.
    - `docs` — spec documents, API references, design guides.
      Text extracted and used for feature/behavior grounding.
    - `counter-example` — NOT what you're building. Used for
      contrast in investigation only.
    - `ignore` — present in `ref/` but duplo should not use it.
  A single file may have multiple roles — a demo video is often
  both `behavioral-target` (for verification) and `visual-target`
  (for design extraction). Write them comma-separated:
  `role: behavioral-target, visual-target`.
- `notes:` — optional free-form prose.

Optional section. If `## Sources` or `## Purpose` cover what you
want built, `ref/` can be empty and this section can be left
blank.

Example:

    - ref/numi-main.png
      role: visual-target
      notes: Primary reference for layout, colors, typography.

    - ref/demo.mp4
      role: behavioral-target
      notes: Authoritative for expression syntax and result display.

    - ref/internal-notes.md
      role: ignore

#### Proposed entries

When you add files to `ref/` and re-run duplo, duplo runs Vision
on them and proposes role entries:

    - ref/new-mockup.png
      role: visual-target
      proposed: true
      notes: Proposed by duplo on 2026-04-12. Appears to show a
             settings panel. Edit role or delete this entry if wrong.

duplo will not act on `proposed: true` entries until you remove
the flag. This means inference is always visible and reviewable
before it influences a build.


### `## Architecture`

Language, framework, platform, dependency constraints. This is
injected into roadmap and plan generation prompts and supplements
duplo's interactive build preferences.

Required. Leave the `<FILL IN>` marker in place if you don't
have an answer yet — duplo will refuse to run.

Examples:

    Swift, SwiftUI, SPM, macOS 14+. No external dependencies.

    Python 3.11+, FastAPI, SQLite. Poetry for packaging.
    Pytest for tests. No async until Phase 3.

    TypeScript, React, Vite. No state management library — plain
    useState and useReducer only. Tailwind for styling.


### `## Design`

Visual direction — colors, typography, spacing, overall aesthetic.
Injected into plan generation prompts.

Optional in the sense that you can leave the `<FILL IN>` marker
as-is if `## References` includes `visual-target` files. duplo
will extract design details from those files via Vision and append
an `AUTO-GENERATED` block to this section:

    ## Design

    <FILL IN: colors, typography, aesthetic — or leave as-is>

    <!-- BEGIN AUTO-GENERATED: design details extracted from ref/
         images. Safe to edit; duplo will not overwrite manual
         changes. -->
    Colors (from ref/numi-main.png):
      - primary: #2b2b2b
      - accent: #a3d977
      - text: #e0e0e0

    Typography:
      - monospace body, ~14px, system default
    <!-- END AUTO-GENERATED -->

If you write your own design prose above the `AUTO-GENERATED`
block, your prose takes precedence when the two conflict.

If you have no `visual-target` files AND leave `<FILL IN>` as-is,
duplo will warn at run time that design has no input source.


### `## Scope`

Overrides for what duplo should include or exclude, regardless of
what scraping or reference analysis produces.

- `include:` — features duplo should build even if not in scraped
  content. Useful when the spec demands something the product
  reference doesn't offer.
- `exclude:` — features duplo should NOT build even if scraped.
  Useful when scraping picks up things outside your scope.

Optional section.

Example:

    include:
      - Unit conversion
      - Variable assignment
      - Custom keybindings

    exclude:
      - JavaScript plugin API
      - Alfred workflow integration
      - Cloud sync


### `## Behavior`

Input → output pairs duplo should turn into verification tasks in
PLAN.md. Each pair becomes a concrete test case the build is
checked against.

Optional but highly recommended. Behavioral contracts are the most
reliable way to pin down what "correct" means for the resulting
build, especially for apps with complex parsing or computation.

Format: `` `input` → `expected output` `` per line. Both the
input and expected output must be wrapped in backticks. Labels
like "input:" / "output:" are not used — the parser keys on the
backtick pairs.

Example:

    - `2 + 3` → `5`
    - `Price: $7 × 4` → `$28`
    - `5 km in miles` → `3.11 mi`
    - `today + 3 days` → `(today's date + 3, formatted)`
    - empty input → empty result, no error


### `## Notes`

Free-form prose for anything that doesn't fit the typed sections.
Injected into LLM calls as general guidance.

Optional. Use for:

- Constraints that don't fit `## Architecture` (e.g. "must work
  offline", "must run on battery without spinning the fan").
- Preferences about code style, error handling, logging.
- Context about why you're building this (helps the LLM make
  sensible defaults when the spec is silent).
- Anything you'd say to a human collaborator that the typed
  sections don't capture.


## What duplo writes vs. what you write

| Section          | duplo drafts | duplo appends    | duplo modifies |
|------------------|--------------|------------------|----------------|
| `## Purpose`     | yes (init)   | no               | no             |
| `## Sources`     | yes (init)   | yes (discovered) | no             |
| `## References`  | yes (init)   | yes (proposed)   | no             |
| `## Architecture`| yes (init)   | no               | no             |
| `## Design`      | yes (init)   | yes (auto-gen)   | no             |
| `## Scope`       | no           | no               | no             |
| `## Behavior`    | no           | no               | no             |
| `## Notes`       | no           | no               | no             |

"yes (init)" means duplo may pre-fill the section during
`duplo init` based on a URL scrape or prose description. Once
`duplo init` finishes, the user owns those sections and duplo
won't touch them on later runs.

"yes (discovered/proposed/auto-gen)" means duplo may append
specifically-flagged entries on later runs. The flags make the
inference visible. duplo will not act on flagged entries until
the user removes the flag.


## Common patterns

### URL alone

```markdown
## Purpose
A macOS menu bar calculator like numi.

## Sources
- https://numi.app
  role: product-reference
  scrape: deep

## Architecture
Swift, SwiftUI, SPM, macOS 14+. No external dependencies.
```

`ref/` is empty. duplo extracts everything from the scrape.

### Prose alone

```markdown
## Purpose
A CLI that converts Markdown files to a flat JSON AST.

## Architecture
Python 3.11+, no dependencies beyond stdlib. Poetry for packaging.

## Behavior
- `# Hello\n\nWorld` → `{"type":"doc","children":[{"type":"heading","level":1,"text":"Hello"},{"type":"paragraph","text":"World"}]}`
- empty file → `{"type":"doc","children":[]}`

## Notes
Must handle GFM tables and footnotes. Do not handle raw HTML.
```

No URL, no `ref/` files. duplo plans entirely from prose.

### URL + visual override

```markdown
## Purpose
A calculator like numi but with a light theme and larger fonts
for accessibility.

## Sources
- https://numi.app
  role: product-reference
  scrape: deep

## References
- ref/light-theme-mockup.png
  role: visual-target
  notes: Use these colors and font sizes, not numi's.

## Architecture
Swift, SwiftUI, SPM, macOS 14+.
```

duplo extracts behavior from numi.app but design from the mockup.


## How SPEC.md relates to duplo's internal state

duplo keeps its own state in `.duplo/`:

- `duplo.json` — extracted features (with status), phase history,
  roadmap, scrape timestamps, issues.
- `product.json` — confirmed product identity.
- `references/` — derived artifacts (filtered video frames,
  processed reference copies).
- `raw_pages/` — cached scraped HTML.
- `examples/` — extracted code examples.
- `file_hashes.json` — change detection.

SPEC.md is intent (you own). `.duplo/` is extraction state (duplo
owns). They intersect:

- `## Scope include/exclude` filters duplo.json's features list.
- `## Behavior` becomes verification tasks alongside duplo.json's
  extracted features.
- `## Design` prose plus duplo.json's extracted design requirements
  both flow into PLAN.md generation.
- `## Sources` authorizes duplo to scrape URLs; duplo.json records
  what was actually scraped (with hashes and timestamps).
- `## References` assigns roles to files in `ref/`; duplo routes
  them to the appropriate pipeline stage based on role.

You edit SPEC.md. duplo manages `.duplo/`. The two stay in sync
because every `duplo` run reads SPEC.md fresh and updates
`.duplo/` accordingly.


## When duplo refuses to run

duplo will print an error and refuse to run when:

- `## Purpose` still contains `<FILL IN>`.
- `## Architecture` still contains `<FILL IN>`.
- No URL in `## Sources`, no file in `## References`, AND
  `## Purpose` + `## Architecture` are too sparse to plan from.

`proposed: true` and `discovered: true` entries do NOT block
execution. duplo simply ignores them in pipeline stages until
you remove the flag. You'll see warnings in the run summary
listing how many unreviewed entries exist.

Edit SPEC.md to fix the issue and run duplo again.
