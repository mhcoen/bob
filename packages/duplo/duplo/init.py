"""Entry point for ``duplo init``.

Implements the command-surface contract from INIT-design.md. The
full drafting pipeline is wired up in later subphases; this module
currently exposes :func:`run_init` as the dispatch target so
``main.py`` has something concrete to call.
"""

from __future__ import annotations

import argparse


def run_init(args: argparse.Namespace) -> None:
    """Run the ``duplo init`` flow.

    Args:
        args: Parsed argparse namespace with fields ``url``,
            ``from_description``, ``deep``, and ``force``.
    """
    raise NotImplementedError("duplo init is not yet implemented")
