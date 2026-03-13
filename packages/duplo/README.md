# Duplo

Duplo duplicates apps, customized however you want. Give it reference
material and it generates a build plan. You then run
[McLoop](https://github.com/mhcoen/mcloop) to build it.

## How it works

Point duplo at a product URL:

```bash
mkdir ~/proj/my-app
cd ~/proj/my-app
duplo https://example.com/product
```

Or drop reference materials into the directory (screenshots, PDFs,
text files with notes) and run `duplo` with no arguments. If any
text file in the directory contains a URL, Duplo will find and
scrape it. Be careful with this: Duplo extracts URLs from every
readable file, so avoid placing files with URLs you don't want
crawled (like notes with GitHub links) in the project directory.

Duplo scans the directory, analyzes everything it finds, identifies
the product, extracts features and visual design details, asks which
features you want, and generates a phased build plan. You then run
mcloop to build it.

When you test the result and find things missing or wrong, drop more
reference material into the directory (a screenshot showing the right
colors, a PDF of the full docs, notes about what to fix) and run
`duplo` again. It detects the new files, re-scrapes the original
product URL to pick up any site changes, re-extracts features from
the updated content, and appends tasks to the plan for anything that
was missed.

The cycle is: run duplo to generate the plan, run mcloop to build
it, test, add more reference material if needed, run duplo again.

```bash
duplo https://example.com # Analyze, extract features, generate PLAN.md
mcloop                    # Build it (runs until all tasks complete)
# ... test the result ...
duplo                     # Detect gaps, generate next phase
mcloop                    # Build the next phase
```

## What Duplo does on first run

1. **Scans reference materials.** Images (png, jpg, gif, webp), videos
   (mp4, mov, webm, avi), PDFs, text/markdown files, and any file
   containing URLs. Each file is assessed for relevance (tiny images
   and empty files are flagged).

2. **Extracts frames from videos.** If video files are present and
   ffmpeg is installed, extracts frames at scene-change points,
   deduplicates near-identical frames using perceptual hashing, and
   filters them with Claude Vision to keep only clear UI screenshots.
   Each accepted frame is described (e.g., "settings panel", "main
   dashboard") and stored in `.duplo/references/`.

3. **Validates the product URL.** Checks that the URL points to a
   single clear product, not a company portfolio or homepage with
   multiple products. If ambiguous, asks you to clarify.

4. **Confirms the product.** States what it thinks it's duplicating
   and gets your confirmation before proceeding.

5. **Extracts visual design from images.** Sends reference screenshots
   and accepted video frames to Claude Vision to extract colors, fonts,
   spacing, layout, and component styles. These become design
   requirements in the build plan.

6. **Extracts text from PDFs.** Pulls text content from all PDF pages
   and includes it in the feature analysis.

7. **Crawls product documentation.** Follows links from the product
   URL, prioritizing documentation, features, and API references over
   marketing and legal pages. Follows documentation links even if they
   leave the main domain (docs are often hosted separately). Extracts
   code examples as input/expected output pairs, plus feature tables,
   operation lists, and function references.

8. **Downloads embedded media.** Scans fetched HTML pages for
   ``<video>``, ``<source>``, ``<img>``, and ``<picture>`` tags.
   Downloads product screenshots and demo videos to
   ``.duplo/site_media/``. Downloaded videos are frame-extracted
   the same way as user-provided videos. Downloaded images are
   used for design extraction alongside user-provided screenshots.

9. **Extracts features.** Uses Claude to analyze all collected text
   and produce a structured feature list grouped by category.

10. **Interactive selection.** Presents the features and asks which to
   include. Then asks about platform, language, constraints, and
   preferences.

11. **Generates a phased roadmap.** Breaks the selected features into
    phases, starting with the smallest end-to-end working thing. Each
    phase has a title, goal, feature list, and test criteria.

12. **Generates test cases from documentation.** Every code example
    extracted from the docs becomes a unit test case that calls the
    app's core logic directly. Tests are grouped by category.

13. **Generates Phase 1 plan.** Writes a PLAN.md for Phase 1,
    CLAUDE.md, and mcloop.json. Prints "Run mcloop to start
    building." and exits. You run mcloop separately.

14. **Cleans up.** Moves processed reference files to
    `.duplo/references/` and saves a file hash manifest for detecting
    changes on subsequent runs.

## Subsequent runs

Running `duplo` again in the same directory detects what happened
since the last run:

- **Completed phase:** If all tasks in PLAN.md are checked off,
  Duplo records the phase in history, tracks which features were
  implemented, prompts for known issues, and enters the next-phase
  flow. See "Phase completion" and "Next-phase generation" below.

- **Incomplete phase:** If PLAN.md has unchecked tasks, Duplo
  prints a status summary and tells you to run mcloop to continue
  building.

- **No PLAN.md:** Duplo enters the next-phase flow directly.

On every subsequent run, Duplo also re-scrapes the product URL to
pick up site changes, re-extracts features from the updated content,
and merges new features into its stored list (without removing
existing ones).

Duplo prints a status summary at the start of every run: current
phase number, features implemented vs. remaining, and open issue
count.

### Phase completion

When Duplo detects that all tasks in PLAN.md are checked off, it
runs the phase-completion flow:

1. **Track implemented features.** Generated plans include
   `[feat: "Feature Name"]` annotations on each task line linking
   it to features in `duplo.json`. Duplo parses checked lines and
   marks annotated features as `implemented`. Bug fix tasks carry
   `[fix: "description"]` annotations and resolve matching issues.
   Unannotated tasks (added by the user or from pre-annotation
   plans) are matched against the feature list via a single
   `claude -p` call. Matched features are marked as implemented.
   Genuinely new items are added to `duplo.json` as new features
   with `status: "implemented"`.

2. **Collect issues.** Duplo prompts for known problems with the
   completed phase (bugs, incomplete wiring, UI issues). Each
   line is stored in the `issues` list in `duplo.json`. Skippable
   with a blank line.

3. **Record and advance.** The completed plan is appended to the
   phase history in `duplo.json`, PLAN.md is deleted, and Duplo
   falls through to the next-phase flow.

### Next-phase generation

After phase completion (or when no PLAN.md exists), Duplo generates
the next phase:

1. **Re-scrape and re-extract.** Fetches the product site again,
   extracts features from the updated content, and merges new ones
   into `duplo.json`.

2. **Generate roadmap.** If no roadmap exists or the previous one
   has been fully consumed, Duplo generates a new phased roadmap
   from the remaining unimplemented features. The roadmap is
   regenerated at each phase boundary so it always reflects what
   has actually been built.

3. **Feature selection.** Presents the remaining unimplemented
   features (numbered, grouped by category) with the next roadmap
   phase highlighted as a recommendation. You can accept the
   recommendation, modify it, or pick entirely different features.

4. **Issue selection.** Shows open issues from `duplo.json` and
   asks which should be addressed in this phase.

5. **Plan generation.** Generates a PLAN.md scoped to the selected
   features and issues. Every task line is annotated with
   `[feat: ...]` or `[fix: ...]` so the next phase completion can
   track status deterministically. Parent tasks whose subtasks are
   all specific enough to execute without design decisions are
   marked with `[BATCH]` so McLoop combines the subtasks into a
   single session for efficiency.

### Non-destructive updates

The update cycle is non-destructive. Running `duplo` again never
removes or overwrites existing code, plans, or configuration:

- **PLAN.md:** New tasks are appended to the end of the file.
  Existing checked and unchecked items are preserved exactly as
  they are.
- **CLAUDE.md:** Only sections with new headings are appended.
  Sections already present are left untouched.
- **mcloop.json:** New check commands are merged in. Existing
  commands are never removed or modified.
- **README.md:** New sections are appended by heading. Existing
  content is not replaced.
- **Code and project files:** Duplo never modifies files that
  McLoop or the user created. It only writes to its own state
  directory (`.duplo/`) and the configuration files above.

This means you can safely re-run `duplo` at any point without
losing work. Add more reference material, run duplo, and only
new tasks for uncovered features or design refinements are added.

All state lives in `.duplo/` (added to `.gitignore` automatically):
`duplo.json` for selections, features (with implementation status),
phases, issues, roadmap, and preferences; `references/` for
processed reference files; `examples/` for extracted code examples;
`raw_pages/` for scraped HTML content; `file_hashes.json` for
change detection.

## Feature tracking

Duplo maintains a persistent feature list in `duplo.json`. Each
feature has a name, description, category, implementation status
(`pending`, `implemented`, or `partial`), and which phase
implemented it. Features come from three sources:

- **Product scraping.** Extracted from the product site on first
  run and re-extracted on every subsequent run. New features are
  merged without removing existing ones.

- **Plan annotations.** When a generated plan includes
  `[feat: "Feature Name"]` on a task line, phase completion
  marks that feature as implemented.

- **User additions.** Any task line in PLAN.md without an
  annotation is treated as user-added. At phase completion,
  Duplo matches it against existing features or adds it as a
  new entry.

This means the feature list is a complete record of everything
built across all phases, not just what was scraped from the
product site.

## Requirements

- Python 3.11+
- [McLoop](https://github.com/mhcoen/mcloop) (`pip install -e ~/proj/mcloop`)
- `claude` CLI on PATH
- macOS for appshot screenshot verification
- [Playwright](https://playwright.dev/) for reference screenshots
  (`playwright install chromium` after pip install). Only needed on
  first run if the product URL has documentation pages to screenshot.
- [ffmpeg](https://ffmpeg.org/) on PATH for video frame extraction
  (optional). Install with `brew install ffmpeg`. If not installed,
  video files are skipped with a warning.

## Install

```bash
git clone https://github.com/mhcoen/duplo.git
cd duplo
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Development

```bash
ruff check .              # Lint
ruff format --check .     # Format check
pytest                    # Tests
```

## License

MIT. See [LICENSE](LICENSE).

## Author

**Michael H. Coen**
mhcoen@gmail.com | mhcoen@alum.mit.edu
[@mhcoen](https://github.com/mhcoen)
