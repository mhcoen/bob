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
  - [ ] Detect and follow wiki links (GitHub wiki, GitBook, ReadTheDocs, etc.) from the product page
  - [ ] Increase max_pages in fetch_site for documentation-heavy sites (docs pages are small and dense)
  - [ ] Extract code examples from documentation as structured data (input, expected output pairs)
  - [ ] Extract feature tables from documentation (operation tables, unit lists, function lists)
  - [ ] Store extracted examples in duplo.json under an "examples" key
  - [ ] Each example should have: category, input, expected_output, source_url
- [ ] Test case generation
  - [ ] Generate unit test cases from extracted documentation examples
  - [ ] For each example with an input/output pair, create a test that calls the evaluate function directly (no GUI needed)
  - [ ] Write test cases to the target project as a test file (e.g., Tests/NumiTests/DocExampleTests.swift)
  - [ ] Include test generation as tasks in the generated PLAN.md so McLoop builds them
  - [ ] Group tests by category (arithmetic, units, percentages, variables, etc.)
- [ ] Re-run mode (duplo update)
  - [ ] Add "update" subcommand to CLI: duplo update
  - [ ] Re-scrape the product URL with the improved deep extractor
  - [ ] Compare newly extracted features against existing features in duplo.json
  - [ ] Compare extracted examples against existing test coverage in the target project
  - [ ] Append new unchecked tasks to the existing PLAN.md for missing features
  - [ ] Append new test tasks for uncovered documentation examples
  - [ ] Do not modify or remove any existing checked or unchecked tasks
  - [ ] Print a summary of what was added
