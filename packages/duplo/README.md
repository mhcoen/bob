# Duplo

Duplo duplicates apps. Point it at a product website and it scrapes the
features, asks which ones you want, generates a phased build plan, and
uses [McLoop](https://github.com/mhcoen/mcloop) to build each phase
autonomously. Between phases it asks you to test, takes your feedback,
and revises the plan for the next round.

The goal is magic: you provide a URL and answer a few questions, then
walk away while Duplo builds a working clone of the product.

## How it works

1. `duplo https://example.com/product` scrapes the product page, docs,
   screenshots, and feature lists.
2. Duplo presents features and asks which ones you want, what platform,
   what constraints.
3. It generates Phase 1 (the smallest thing that works end to end),
   creates the project directory, writes PLAN.md, inits git, and runs
   McLoop.
4. When Phase 1 completes, Duplo notifies you and waits for feedback.
5. You test, give feedback. Duplo revises the plan for Phase 2,
   incorporating your feedback alongside the next batch of features.
6. Repeat until done.

## Visual QA

Duplo includes `appshot`, a utility for capturing deterministic
screenshots of macOS app windows. After each phase, Duplo can capture
the current state of the app and compare it against reference
screenshots from the target product to identify visual issues.

```bash
bin/appshot "AppName" screenshot.png
bin/appshot "AppName" screenshot.png --launch .build/debug/AppName
bin/appshot "AppName" screenshot.png --setup 'tell application "AppName" to activate'
```

## Install

```bash
git clone https://github.com/mhcoen/duplo.git
cd duplo
python -m venv .venv
source .venv/bin/activate
pip install -e .
chmod +x bin/appshot
```

## Requirements

- Python >= 3.11
- McLoop (`pip install mcloop`)
- `claude` CLI on PATH
- macOS (for appshot and native app building)
- Screen Recording permission (for appshot, granted once)

## Author

**Michael H. Coen**
mhcoen@gmail.com | mhcoen@alum.mit.edu
[@mhcoen](https://github.com/mhcoen)
