"""Shared pytest configuration.

Integration tests (marked with @pytest.mark.integration) are skipped
unless the MCLOOP_INTEGRATION environment variable is set.
"""

from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("MCLOOP_INTEGRATION"):
        return
    skip = pytest.mark.skip(reason="MCLOOP_INTEGRATION not set")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)
