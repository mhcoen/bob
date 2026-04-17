"""Entry point for ``duplo init``.

Implementation shape for the ``duplo init`` subcommand per
INIT-design.md § "Implementation shape": a single module exposing
one :func:`run_init` entry point. ``duplo/main.py`` dispatches here
when ``sys.argv[1] == "init"``.

The body of :func:`run_init` is fleshed out in subsequent subphases
(no-arguments case, URL-only case, ``--from-description`` case, and
the combined case). This stub keeps the dispatch target importable
so the argparse wiring in ``main.py`` has something concrete to
call while the rest of the flow is being built.
"""

from __future__ import annotations

import argparse

from duplo.fetcher import fetch_site  # noqa: F401
from duplo.scanner import scan_directory  # noqa: F401
from duplo.spec_writer import draft_spec, format_spec  # noqa: F401
from duplo.validator import validate_product_url  # noqa: F401


def run_init(args: argparse.Namespace) -> None:
    """Run the ``duplo init`` flow.

    The single entry point for the subcommand. Orchestrates the
    narrow init pipeline: URL validation, shallow (or deep) scrape,
    existing ``ref/`` inventory, drafter invocation, and SPEC.md
    write-out. Delegates to existing utilities; see INIT-design.md
    § "Implementation shape" for the dependency list.

    Args:
        args: Parsed argparse namespace with fields ``url``,
            ``from_description``, ``deep``, and ``force``.
    """
    raise NotImplementedError("duplo init is not yet implemented")
