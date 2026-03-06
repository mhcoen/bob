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
features you want, and generates a build plan. McLoop then builds it.

When you test the result and find things missing or wrong, drop more
reference material into the directory (a screenshot showing the right
colors, a PDF of the full docs, notes about what to fix) and run
`duplo` again. It detects the new files, re-analyzes, and appends
tasks to the plan for anything that was missed.

The cycle is: add reference material, run duplo, let McLoop build,
test, add more if needed, run duplo again.

## Install

```bash
git clone https://github.com/mhcoen/duplo.git
cd duplo
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Requires McLoop (`pip install -e ~/proj/mcloop`), Python 3.11+,
`claude` CLI on PATH, and macOS for native app building.

## Author

**Michael H. Coen**
mhcoen@gmail.com | mhcoen@alum.mit.edu
[@mhcoen](https://github.com/mhcoen)
