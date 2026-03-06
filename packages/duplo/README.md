# Duplo

Duplo duplicates apps, customized however you want. Give it reference
material and it builds a working version using
[McLoop](https://github.com/mhcoen/mcloop).

## How it works

Create a project directory. Drop in whatever you have: screenshots of
the app, PDFs of the docs, text files with notes, a file containing
the product URL. Run `duplo` from that directory.

```bash
mkdir ~/proj/my-app
cd ~/proj/my-app
# Drop in reference material: screenshots, PDFs, URLs...
echo "https://example.com/product" > url.txt
duplo
```

Duplo scans the directory, analyzes everything it finds, identifies
the product, extracts features and visual design details, asks which
features you want, and generates a phased build plan. McLoop then
builds it phase by phase.

When you test the result and find things missing or wrong, drop more
reference material into the directory (a screenshot showing the right
colors, a PDF of the full docs, notes about what to fix) and run
`duplo` again. It detects the new files, re-analyzes, and appends
tasks to the plan for anything that was missed.

The cycle is: add reference material, run duplo, let McLoop build,
test, add more if needed, run duplo again.

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

8. **Extracts features.** Uses Claude to analyze all collected text
   and produce a structured feature list grouped by category.

9. **Interactive selection.** Presents the features and asks which to
   include. Then asks about platform, language, constraints, and
   preferences.

10. **Generates a phased roadmap.** Breaks the selected features into
    phases, starting with the smallest end-to-end working thing. Each
    phase has a title, goal, feature list, and test criteria.

11. **Generates test cases from documentation.** Every code example
    extracted from the docs becomes a unit test case that calls the
    app's core logic directly. Tests are grouped by category.

12. **Builds Phase 1.** Generates a PLAN.md for Phase 1, writes
    CLAUDE.md and mcloop.json, and runs McLoop to build it.

13. **Captures and compares screenshots.** After McLoop finishes,
    uses appshot to capture a screenshot of the built app and compares
    it against reference images. Visual differences are saved to
    ISSUES.md.

14. **Cleans up.** Moves processed reference files to
    `.duplo/references/` and saves a file hash manifest for detecting
    changes on subsequent runs.

## Subsequent runs

Running `duplo` again in the same directory detects what happened
since the last run:

- **Interrupted phase:** If McLoop was killed mid-phase, Duplo
  resumes where it left off. If McLoop finished but the post-phase
  steps (screenshots, comparison) didn't complete, those are resumed.

- **Phase complete:** Duplo collects your feedback (text input or
  from a file), incorporates it along with any visual issues, and
  generates the next phase's PLAN.md. McLoop then builds it.

- **New files detected:** Duplo compares the directory against its
  stored file hash manifest and reports what changed (added, modified,
  removed files).

All state lives in `.duplo/` (added to `.gitignore` automatically):
`duplo.json` for selections, features, phases, and preferences;
`references/` for processed reference files; `examples/` for
extracted code examples; `raw_pages/` for scraped HTML content;
`file_hashes.json` for change detection.

## Install

```bash
git clone https://github.com/mhcoen/duplo.git
cd duplo
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Requirements

- Python 3.11+
- [McLoop](https://github.com/mhcoen/mcloop) (`pip install -e ~/proj/mcloop`)
- `claude` CLI on PATH
- macOS for appshot screenshot verification
- [ffmpeg](https://ffmpeg.org/) on PATH for video reference extraction (optional).
  When video files (mp4, mov, webm, avi) are present in the project directory,
  Duplo uses ffmpeg to extract frames at scene-change points, deduplicates
  them with perceptual hashing, filters them with Claude Vision to keep only
  clear UI screenshots, and includes the accepted frames in design extraction
  alongside user-provided images. Install with `brew install ffmpeg` (macOS),
  `apt install ffmpeg` (Debian/Ubuntu), or download from
  [ffmpeg.org](https://ffmpeg.org/download.html). If ffmpeg is not installed,
  video files are skipped with a warning.

## Development

```bash
ruff check .              # Lint
ruff format --check .     # Format check
pytest                    # Tests
```

## Author

**Michael H. Coen**
mhcoen@gmail.com | mhcoen@alum.mit.edu
[@mhcoen](https://github.com/mhcoen)
