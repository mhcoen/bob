"""Adapter set: the four-operation contract plus the slice-1 mocks."""

from orchestra.adapters.base import Adapter
from orchestra.adapters.mock_human import MockHumanAdapter
from orchestra.adapters.mock_model import MockModelAdapter
from orchestra.adapters.mock_shell import MockShellAdapter

__all__ = [
    "Adapter",
    "MockHumanAdapter",
    "MockModelAdapter",
    "MockShellAdapter",
]
