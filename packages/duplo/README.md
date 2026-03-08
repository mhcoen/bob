# Duplo

Duplo duplicates apps, customized however you want. Give it reference
material and it generates a build plan. You then run
[McLoop](https://github.com/mhcoen/mcloop) to build it.

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
features you want, and generates a phased build plan. You then run
mcloop to build it.

When you test the result and find things missing or wrong, drop more
reference material into the directory (a screenshot showing the right
colors, a PDF of the full docs, notes about what to fix) and run
`duplo` again. It detects the new files, re-analyzes, and appends
tasks to the plan for anything that was missed.

The cycle is: run duplo to generate the plan, run mcloop to build
it, test, add more reference material if needed, run duplo again.

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
  Duplo records the phase in history, captures screenshots for
  comparison, collects your feedback, and generates the next
  phase's PLAN.md. You then run mcloop again.

- **Incomplete phase:** If PLAN.md has unchecked tasks, Duplo
  tells you to run mcloop to continue building.

- **New files detected:** Duplo compares the directory against its
  stored file hash manifest and reports what changed (added, modified,
  removed files).

- **Re-scrapes the product URL.** If the product site has changed,
  Duplo fetches it again, re-extracts features from the updated
  content, and merges new features into its stored feature list
  (without removing existing ones). The gap detector then compares
  the combined feature list against PLAN.md.

- **Gap detection is platform-aware.** When comparing features against
  the plan, Duplo tells Claude the project's target platform and
  language. Features that are infeasible for the target stack (e.g.,
  "Windows support" for a macOS-only SwiftUI app) are skipped rather
  than appended as tasks.

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

## Important: URLs in reference files

Duplo extracts HTTP(S) URLs from **every** readable file in the
project directory, not just files with a `.txt` extension. If your
`plan.txt` or any other text file contains GitHub URLs, documentation
links, or other URLs you don't want scraped, Duplo will attempt to
fetch and analyze them as product pages. Keep the product URL in
`urls.txt` and avoid embedding URLs in other reference files unless
you want them crawled.

## Requirements

- Python 3.11+
- [McLoop](https://github.com/mhcoen/mcloop) (`pip install -e ~/proj/mcloop`)
- `claude` CLI on PATH
- macOS for appshot screenshot verification
- [Playwright](https://playwright.dev/) for reference screenshots
  (`playwright install chromium` after pip install). Only needed on
  first run if the product URL has documentation pages to screenshot.
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

## License

MIT. See [LICENSE](LICENSE).

## Author

**Michael H. Coen**
mhcoen@gmail.com | mhcoen@alum.mit.edu
[@mhcoen](https://github.com/mhcoen)
