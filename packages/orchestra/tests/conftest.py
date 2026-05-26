"""Workspace-compatible test root.

Adds this directory to ``sys.path`` so that intra-tests imports like
``from helpers.legacy_prompt_manifest import compute_prompt_manifest``
resolve regardless of where pytest is invoked from. The accompanying
removal of ``tests/__init__.py`` is what lets workspace-root pytest
disambiguate this conftest from sibling packages' tests/conftest.py
(see /Users/mhcoen/proj/bob/.scratch/workspace-pytest-fix/diagnosis.md).
"""

from __future__ import annotations

import sys
from pathlib import Path

_TESTS_DIR = str(Path(__file__).resolve().parent)
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)
