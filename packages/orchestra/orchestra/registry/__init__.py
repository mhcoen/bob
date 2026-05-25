"""Profile registry: catalog of profile-registered capabilities.

Slice 1 has no profiles, but the data structure and the conflict
detection are wired in so slice 2 can plug in the versioned-workspace
profile by registration alone.
"""

from orchestra.registry.registry import (
    AdapterFactory,
    ParserFn,
    ProfileRegistry,
    ResultParser,
    ScopePredicate,
)
from orchestra.transforms import (
    Transform,
    TransformCallable,
    TransformContext,
)

__all__ = [
    "AdapterFactory",
    "ParserFn",
    "ProfileRegistry",
    "ResultParser",
    "ScopePredicate",
    "Transform",
    "TransformCallable",
    "TransformContext",
]
