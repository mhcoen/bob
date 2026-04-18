# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Duplo is a CLI that ingests a user-authored `SPEC.md` plus reference materials
dropped into `ref/` (sites, PDFs, videos, screenshots) and emits a `PLAN.md`
that [mcloop](https://github.com/mhcoen/mcloop) executes task-by-task to build
a new application. No source code from the reference product is used — duplo
works from what a user can see.

## Subcommands

- `duplo init [URL] [--from-description FILE] [--force]` — create a starter
  `SPEC.md` and `ref/` directory in cwd. URL pre-fills sections from a live site.
- `duplo` — default: extract features, generate roadmap, emit next phase's `PLAN.md`.
- `duplo fix "<complaint>"` — LLM-backed bug investigation; appends fix tasks to `PLAN.md`.

Duplo operates on the current working directory; it does not create a project dir.

## Running

- Entry point: `duplo` (defined in `pyproject.toml` → `duplo.main:main`)
- Python 3.11+; install with `pip install -e .` inside the project venv
- Dependencies: mcloop, httpx, beautifulsoup4, lxml, playwright, anthropic, pypdf, Pillow
- `duplo/main.py` begins with an mcloop wrapper; real imports start after
  `# mcloop:wrap:end` on line 190. `main.py` is exempt from ruff E402 for that reason.

## Tests & checks

- Full suite: `python -m pytest tests/`
- First-failure debug: `python -m pytest tests/ -x --tb=short`
- Single module: `python -m pytest tests/test_<module>.py -v`
- Single test: `python -m pytest tests/test_<module>.py::test_<name> -v`
- mcloop checks (from `mcloop.json`, run automatically between tasks):
  `ruff check .`, `ruff format --check .`, `pytest`
- Ruff config: `target-version = "py311"`, `line-length = 99`.
  `duplo/main.py` is exempt from E402 (mcloop wrapper forces late imports).

## State & outputs

- Target-project state lives in `.duplo/` (managed by `saver.py`):
  - `.duplo/duplo.json` — runtime state, incl. `preferences` and `architecture_hash`
  - `.duplo/product.json` — product metadata
- Files duplo owns in the target project root (`_PROJECT_FILES` in `main.py`):
  `PLAN.md`, `CLAUDE.md`, `README.md`, `ISSUES.md`, `NOTES.md`, `SPEC.md`.
  Everything else in the target project is treated as user-provided reference material.

## Key modules

- `spec_reader.py` — parses SPEC.md (`## Purpose / Scope / Behavior / Architecture /
  Design / References`). Result is injected into every LLM prompt.
- `build_prefs.py` — extracts structured `BuildPreferences` from the prose
  `## Architecture` section via an LLM call. Cached in `.duplo/duplo.json` and
  invalidated when the SHA-256 of the comment-stripped architecture text changes.
  Replaces the old interactive `questioner.ask_preferences()` flow.
- `planner.py` — generates PLAN.md for a single roadmap phase. mcloop consumes
  one checklist item per task. Aim for 5–15 items; phase 0 is scaffold-only;
  every item must leave the project in a building and runnable state.
- `saver.py` — writes all derived state to `.duplo/` in the target project.
- `investigator.py` — LLM-backed product-level bug diagnosis: compares current
  app state against reference frames, SPEC behavior contracts, and user complaints.
- `orchestrator.py`, `fetcher.py`, `scanner.py`, `frame_*`, `video_extractor.py`,
  `pdf_extractor.py`, `docs_extractor.py` — ingest reference materials from `ref/`.
- `init.py`, `initializer.py` — `duplo init` subcommand (starter SPEC.md + `ref/`).
- `gap_detector.py`, `roadmap.py`, `selector.py`, `extractor.py` — feature extraction,
  phase planning, and gap detection between SPEC and current build.
- `claude_cli.py` — single wrapper for Claude CLI / Anthropic API calls.
- `platforms/` — per-platform scaffold + CLAUDE.md rule profiles (e.g. `macos/swiftui_spm.py`).

## Design docs

Longform design references live in the repo root and are authoritative when in
doubt: `INIT-design.md`, `PIPELINE-design.md`, `PARSER-design.md`,
`DRAFTER-design.md`, `MIGRATION-design.md`, `REDESIGN-overview.md`,
`SPEC-guide.md`, `SPEC-template.md`. `AGENTS.md` describes the agent contracts.

## Architectural rules

- **PLAN.md is the source of truth** for what mcloop builds. Do not hand-edit
  PLAN.md during execution; regenerate it via `duplo`.
- **`BuildPreferences` come from SPEC**, not prompts. Do not reintroduce
  interactive questioning; everything flows through `build_prefs.parse_build_preferences`.
- **SPEC.md is user-owned**; duplo never rewrites it. Derived artifacts go in
  `.duplo/` or the `_PROJECT_FILES` set above.
- **`.duplo/` is the only state directory.** Never scatter JSON elsewhere in
  the target project.
- PLAN.md task descriptions must be **ASCII only** — no backticks, no em-dashes,
  no smart quotes. mcloop parses these literally and non-ASCII breaks the loop.

## Code conventions

- When you modify a module, run its tests: `python -m pytest tests/test_<module>.py`.
- Report failures via `duplo.diagnostics.record_failure`; do not `print` errors.
- Target-project paths always flow from a `target_dir` parameter (default `.`);
  never hard-code paths relative to duplo's own repo.
- Add new functionality in a dedicated module rather than extending `main.py`.
