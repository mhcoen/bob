# Duplo

Duplo duplicates apps by scraping product websites, generating phased
build plans, and orchestrating McLoop to build each phase autonomously.
Between phases it captures screenshots for visual QA, collects user
feedback, and revises the plan for the next round.

Python 3.11+, depends on McLoop. Uses Claude Code via McLoop for all
code generation. Ruff for linting, pytest for tests. Keep modules
short and focused. This is a thin orchestration layer, not a framework.

- [x] Project scaffolding
  - [x] Create duplo package with __init__.py and main.py entry point
  - [x] Add CLI argument parser: duplo <url>, duplo run, duplo next
  - [x] Verify pip install -e . works and duplo command runs
- [x] Product scraping
  - [x] Fetch the product URL and extract text content
  - [x] Follow links, prioritizing documentation, features, guides, changelogs, and API references over marketing, blog, pricing, legal, and login pages
  - [x] Save reference screenshots from the product website
  - [x] Extract a structured feature list from the scraped content
- [x] Interactive feature selection
  - [x] Present features to the user and ask which to include
  - [x] Ask about platform, language, constraints, and preferences
  - [x] Save selections to duplo.json in the target project
- [ ] Plan generation
  - [x] Generate Phase 1 PLAN.md (smallest end-to-end working thing)
  - [x] Create target project directory with git init
  - [ ] Write PLAN.md, README.md, and mcloop.json
  - [ ] Include CLAUDE.md with appshot instructions
- [ ] Phase execution
  - [ ] Run McLoop on the target project
  - [ ] Wait for completion, capture screenshots with appshot
  - [ ] Compare screenshots against reference images via Claude API
  - [ ] Generate visual issue list
  - [ ] Notify user that phase is complete and ready for testing
- [ ] Feedback and iteration
  - [ ] Collect user feedback (text input or from a file)
  - [ ] Generate next phase PLAN.md incorporating feedback and visual issues
  - [ ] Append completed phases to duplo.json history
  - [ ] Run McLoop for the next phase
- [ ] State management
  - [ ] Store all state in duplo.json: source URL, features, phases, feedback
  - [ ] Support resuming after interruption (duplo run picks up where it left off)
  - [ ] Track which reference screenshots map to which features
