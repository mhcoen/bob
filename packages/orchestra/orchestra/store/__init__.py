"""Artifact store package.

The artifact store is the typed, versioned data substrate for a
workflow run. See ``design/orchestra-runner.md``, section "Artifact
store and versioning interface".
"""

from orchestra.store.store import ArtifactStore, StoredVersion

__all__ = ["ArtifactStore", "StoredVersion"]
