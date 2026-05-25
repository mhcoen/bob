"""Shared pytest fixtures for bob_tools.planfile tests."""

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Auto-skip ``slow``-marked tests unless ``-m slow`` was passed.

    Lets the default ``pytest`` invocation stay fast while keeping the
    1000-iteration generative variants one CLI flag away. The 100-iter
    variants of those same tests are unmarked and always run.
    """
    markexpr = config.option.markexpr or ""
    if "slow" in markexpr:
        return
    skip_slow = pytest.mark.skip(reason="slow (run with: pytest -m slow)")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)
