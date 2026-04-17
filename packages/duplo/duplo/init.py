"""Entry point for ``duplo init``.

Implementation shape for the ``duplo init`` subcommand per
INIT-design.md § "Implementation shape": a single module exposing
one :func:`run_init` entry point. ``duplo/main.py`` dispatches here
when ``sys.argv[1] == "init"``.

The URL-only, ``--from-description``, and combined cases are fleshed
out in subsequent subphases; this module currently covers only the
no-arguments flow.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from duplo.fetcher import fetch_site  # noqa: F401
from duplo.scanner import scan_directory  # noqa: F401
from duplo.spec_reader import ProductSpec
from duplo.spec_writer import draft_spec, format_spec  # noqa: F401
from duplo.validator import validate_product_url  # noqa: F401

# Static body of ``ref/README.md`` per INIT-design.md § "ref/README.md
# content".  Written once by :func:`run_init` and never modified by
# duplo afterward.
_REF_README_CONTENT = """# ref/

Drop reference files here that you want duplo to use as
authoritative examples of what you're building.

Accepted file types:
  - Images: png, jpg, gif, webp (UI screenshots, mockups, logos)
  - Videos: mp4, mov, webm, avi (demos, walkthroughs)
  - PDFs: spec documents, design guides, API docs
  - Text/markdown: notes, constraints, requirements

**This directory can be empty.** If SPEC.md's ## Sources section
gives duplo a URL that covers what you want, you don't need any
files here. Add files only when you want to supplement or override
what duplo can learn from the URL.

Each file you add should be listed in SPEC.md's ## References
section with a role (visual-target, behavioral-target, docs,
counter-example, ignore). When you add files and re-run duplo,
duplo will propose role entries for you to confirm or edit.

See SPEC-guide.md (in the project root) for details on each
role and when to use which.
"""

_SPEC_EXISTS_ERROR = (
    "Error: SPEC.md already exists in this directory.\n"
    "  Use `duplo init --force` to overwrite (your existing SPEC.md\n"
    "  will be lost).\n"
    "  Use `duplo` to run against your existing SPEC.md."
)

_NO_ARGS_NEXT_STEPS = """Next steps:
  1. Open SPEC.md in your editor. Replace each <FILL IN> marker
     with your content. See SPEC-guide.md for details on each
     section.
  2. (Optional) Drop reference files into ref/ — screenshots,
     videos, PDFs, design mockups. Skip this if you'll provide
     a URL or rely on prose alone.
  3. (Optional) Add a URL to ## Sources in SPEC.md if you have
     a product to draw from.
  4. Run `duplo` to extract features and generate the build plan."""


def run_init(args: argparse.Namespace) -> None:
    """Run the ``duplo init`` flow.

    The single entry point for the subcommand. Dispatches to the
    appropriate input-combination handler based on ``args.url`` and
    ``args.from_description``. Delegates to existing utilities; see
    INIT-design.md § "Implementation shape" for the dependency list.

    Args:
        args: Parsed argparse namespace with fields ``url``,
            ``from_description``, ``deep``, and ``force``.
    """
    url = getattr(args, "url", None)
    from_description = getattr(args, "from_description", None)
    if url is None and from_description is None:
        _run_no_args(args)
        return
    raise NotImplementedError("duplo init with inputs is not yet implemented")


def _run_no_args(args: argparse.Namespace) -> None:
    """Handle ``duplo init`` with no URL and no ``--from-description``.

    Per INIT-design.md § "duplo init (no arguments)": writes a
    template-only SPEC.md (via ``format_spec`` on an empty
    ``ProductSpec``), creates the ``ref/`` directory if absent, and
    writes ``ref/README.md`` if absent.  Honors ``--force`` to
    overwrite an existing SPEC.md; otherwise errors and exits 1.
    """
    cwd = Path.cwd()
    spec_path = cwd / "SPEC.md"
    force = bool(getattr(args, "force", False))

    if spec_path.exists() and not force:
        print(_SPEC_EXISTS_ERROR, file=sys.stderr)
        sys.exit(1)

    ref_dir = cwd / "ref"
    ref_created = not ref_dir.exists()
    ref_dir.mkdir(exist_ok=True)
    if ref_created:
        print("Created ref/ (empty).")

    readme_path = ref_dir / "README.md"
    if not readme_path.exists():
        readme_path.write_text(_REF_README_CONTENT)
        print("Created ref/README.md.")

    spec_path.write_text(format_spec(ProductSpec()))
    print("Wrote SPEC.md (template, no inputs).")
    print()
    print(_NO_ARGS_NEXT_STEPS)
