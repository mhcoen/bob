# Duplo

Duplo duplicates apps, customized however you want. Give it a URL,
tell it what matters to you, and it builds a working version using
[McLoop](https://github.com/mhcoen/mcloop).

```bash
duplo init https://superwhisper.com
```

Duplo scrapes the product page and docs, asks you which features matter
and what platform to target, then generates a phased build plan. Each
phase produces something runnable. You test it, give feedback, and Duplo
revises the plan for the next round. McLoop handles all the building.

## Workflow

1. You provide a product URL.
2. Duplo scrapes features, docs, and screenshots.
3. You pick what to include and set constraints.
4. Duplo generates Phase 1: the smallest thing that works end to end.
5. McLoop builds it.
6. You test. You give feedback.
7. Duplo generates Phase 2, incorporating your feedback.
8. Repeat until done.

All state lives in `duplo.json` in the target project: source URL,
selected features, phase history, and your feedback. If interrupted,
`duplo run` picks up where it left off.

## Commands

```bash
duplo init <url>   # Scrape URL, select features, save duplo.json + screenshots
duplo run          # Generate Phase 1 PLAN.md from duplo.json (run inside project dir)
duplo next         # Generate and run the next phase (coming soon)
```

## Install

```bash
git clone https://github.com/mhcoen/duplo.git
cd duplo
python -m venv .venv
source .venv/bin/activate
pip install setuptools
pip install --no-build-isolation -e .
```

Requires McLoop (`pip install -e ~/proj/mcloop`), Python 3.11+,
`claude` CLI on PATH, and an `ANTHROPIC_API_KEY` environment variable.

## Author

**Michael H. Coen**
mhcoen@gmail.com | mhcoen@alum.mit.edu
[@mhcoen](https://github.com/mhcoen)
