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
- [x] Plan generation
  - [x] Generate Phase 1 PLAN.md (smallest end-to-end working thing)
  - [x] Create target project directory with git init
  - [x] Write PLAN.md, README.md, and mcloop.json
  - [x] Include CLAUDE.md with appshot instructions
- [x] Phase execution
  - [x] Run McLoop on the target project
  - [x] Wait for completion, capture screenshots with appshot
  - [x] Compare screenshots against reference images via Claude API
  - [x] Generate visual issue list
  - [x] Notify user that phase is complete and ready for testing
- [x] Feedback and iteration
  - [x] Collect user feedback (text input or from a file)
  - [x] Generate next phase PLAN.md incorporating feedback and visual issues
  - [x] Append completed phases to duplo.json history
  - [x] Run McLoop for the next phase
- [x] State management
  - [x] Store all state in duplo.json: source URL, features, phases, feedback
  - [x] Support resuming after interruption (duplo run picks up where it left off)
  - [x] Track which reference screenshots map to which features
- [ ] Deep documentation extraction
  - [x] When scraping a product site, identify links to documentation pages by reading the page content and link text, not by matching a hardcoded list of platforms
  - [x] Follow documentation links even if they leave the main domain (docs are often hosted separately)
  - [x] Increase the page limit for documentation sites since doc pages are individually small but collectively important
  - [ ] Extract code examples from documentation pages as input/expected_output pairs
  - [ ] Extract feature tables, operation lists, unit lists, and function references
  - [ ] Store all extracted examples in duplo.json so they persist across runs
- [ ] Test case generation from documentation
  - [ ] Every input/output example extracted from documentation becomes a unit test case
  - [ ] Tests should call the app's core logic directly without requiring GUI interaction
  - [ ] Include test generation tasks in the PLAN.md that Duplo generates for the target project
  - [ ] Group tests by category so failures are easy to diagnose
- [ ] Persistent state in .duplo/ directory
  - [ ] Create a .duplo/ directory in the target project for Duplo's working state between runs
  - [ ] Save all reference URLs consulted during scraping, with timestamps and content hashes
  - [ ] Save raw scraped content so re-runs can diff against what changed on the product site
  - [ ] Save extracted examples separately from duplo.json so they can be reviewed and edited
  - [ ] Add .duplo/ to the target project's .gitignore
- [ ] Re-run mode (duplo update)
  - [ ] Add "update" subcommand that works on an existing project
  - [ ] Re-scrape the product URL and extract features and examples with the improved extractor
  - [ ] Use .duplo/ state to identify what is new vs what was already seen
  - [ ] Compare against what is already in duplo.json and PLAN.md
  - [ ] Append new unchecked tasks to PLAN.md for any missing features or uncovered examples
  - [ ] Never modify or remove existing tasks (checked or unchecked)
  - [ ] Print a summary of what was added
