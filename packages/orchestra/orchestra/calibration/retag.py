"""Auditable correction of polluted ``run_meta.json`` classifier tags.

The pre-fix expected.txt parser read the entire file and stored the
result in ``tags.expected_stuck``. When expected.txt contained
prose, this polluted the tag with the whole file. ``retag_polluted_meta``
overwrites the tag with the correct one-token classifier while
recording the prior length and the correction timestamp so the
correction is auditable. See REPORT.md Addendum 6 for the failure
mode that motivated this helper.

Also exposes a ``main`` CLI for one-shot corrections:

    python -m orchestra.calibration.retag <run_meta.json> <classifier>

where ``<classifier>`` is one of {positive, negative, ambiguous}.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

from orchestra.calibration.helpers import VALID_EXPECTED


class RetagResult(NamedTuple):
    """Outcome of a retag attempt on a single run_meta.json file."""

    changed: bool
    prior_len: int
    new_len: int


def retag_polluted_meta(meta_path: Path, classifier: str) -> RetagResult:
    """Overwrite ``tags.expected_stuck`` in ``meta_path`` with ``classifier``.

    ``classifier`` must be one of {positive, negative, ambiguous}.

    On overwrite, records two auditability fields under tags:
    ``expected_stuck_pollution_len`` (the prior string's length) and
    ``expected_stuck_corrected_at`` (an ISO-8601 UTC timestamp).

    Returns a ``RetagResult`` describing whether a change happened.
    No-ops (returns changed=False) when the prior tag already equals
    the supplied classifier or when meta_path is missing.
    """
    if classifier not in VALID_EXPECTED:
        raise ValueError(
            f"classifier must be one of {VALID_EXPECTED}, got {classifier!r}"
        )
    if not meta_path.is_file():
        return RetagResult(False, 0, 0)
    data: dict[str, Any] = json.loads(meta_path.read_text())
    tags: dict[str, Any] = data.setdefault("tags", {})
    prior = tags.get("expected_stuck", "")
    prior_len = len(prior) if isinstance(prior, str) else 0
    if prior == classifier:
        return RetagResult(False, prior_len, len(classifier))
    tags["expected_stuck"] = classifier
    tags["expected_stuck_corrected_at"] = (
        datetime.now(UTC).isoformat()
    )
    tags["expected_stuck_pollution_len"] = prior_len
    meta_path.write_text(json.dumps(data, indent=2) + "\n")
    return RetagResult(True, prior_len, len(classifier))


def main(argv: list[str] | None = None) -> int:
    """CLI: retag a single run_meta.json file."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("meta_path", type=Path)
    parser.add_argument("classifier", choices=VALID_EXPECTED)
    args = parser.parse_args(argv)

    result = retag_polluted_meta(args.meta_path, args.classifier)
    tag = "RETAGGED" if result.changed else "unchanged"
    sys.stderr.write(
        f"  {tag} {args.meta_path}: "
        f"prior_len={result.prior_len} -> "
        f"new={result.new_len} ({args.classifier})\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
