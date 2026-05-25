"""Calibration runtime for stress-testing F2-style judge behavior.

Promoted from /tmp/orchestra-phase2/ during Phase 2 closure
(REPORT.md Addendum 6). The runners and helpers encode
calibration-data-integrity guarantees discovered during Phase 2:

- ``clean_stale_versioned_artifacts`` removes per-cycle artifact
  dumps from prior runs before re-populating logs/, preventing
  phantom-trajectory pollution in the matrix extractor.
- ``read_expected_classifier`` enforces a strict first-nonempty-line
  contract on expected.txt and rejects (rather than warns) on
  malformed input. The prior whole-file read polluted run_meta.json
  with thousands of bytes of prose tags.
- ``retag_polluted_meta`` overwrites a polluted ``tags.expected_stuck``
  with the correct token while recording the prior length and the
  correction timestamp under tags for auditability.

The two runners (``iterate_runner``, ``prji_runner``) and the
analysis script (``extract_labels``) are reference implementations
preserved as-is from the Phase 2 sandbox. They take a scenario
directory as their argument and write trajectory artifacts under
that directory. Each calibration run authors its own scenarios; the
scenario directories themselves are not committed.
"""

from orchestra.calibration.helpers import (
    VALID_EXPECTED,
    clean_stale_versioned_artifacts,
    read_expected_classifier,
)
from orchestra.calibration.retag import retag_polluted_meta

__all__ = [
    "VALID_EXPECTED",
    "clean_stale_versioned_artifacts",
    "read_expected_classifier",
    "retag_polluted_meta",
]
