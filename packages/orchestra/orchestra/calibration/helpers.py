"""Calibration data-integrity helpers.

These two helpers encode guarantees discovered during Phase 2 (see
REPORT.md Addendum 6):

- A re-run with fewer cycles than a prior run can leave per-cycle
  artifact dumps from the prior run in logs/. The matrix extractor
  walks logs/ and would count them as phantom cycles. Cleaning at
  the start of each run prevents this.
- expected.txt's classifier token must be parsed strictly. The prior
  whole-file ``.read_text().strip()`` warned-and-proceeded on
  multi-line input and silently polluted run_meta.json with
  thousands of bytes of prose. The new contract is: first nonempty
  line must be exactly one of {positive, negative, ambiguous};
  reject (raise) on malformed input.

Both helpers are pure and have no orchestra dependencies, so they
import cheaply and are testable as plain unit tests.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

VALID_EXPECTED: Final[tuple[str, str, str]] = (
    "positive",
    "negative",
    "ambiguous",
)

# Files in logs/ that are emitted per-cycle, named <artifact>_<N>.<ext>.
# A re-run with fewer cycles than a prior run leaves higher-numbered
# files behind; the matrix extractor walks logs/ and would count them
# as phantom cycles. Excludes log.jsonl, run_meta.json, config.json,
# task.md, history.md, summary.md, progress.jsonl (none of those
# match the pattern), and importantly implement_<N>_stat.txt (the
# "_stat" suffix follows _<digits>, so the regex does not match).
_VERSIONED_ARTIFACT_RE: Final[re.Pattern[str]] = re.compile(r"^.+_\d+\.(?:json|txt|diff)$")


def clean_stale_versioned_artifacts(logs_dir: Path) -> int:
    """Remove per-cycle artifact dumps from prior runs in ``logs_dir``.

    Returns the number of files removed. Idempotent (a second call
    on the same directory removes zero files).
    """
    if not logs_dir.is_dir():
        return 0
    removed = 0
    for child in logs_dir.iterdir():
        if not child.is_file():
            continue
        if _VERSIONED_ARTIFACT_RE.match(child.name):
            child.unlink()
            removed += 1
    return removed


class ExpectedClassifierError(ValueError):
    """Raised when expected.txt is missing, empty, or malformed."""


def read_expected_classifier(expected_path: Path) -> str:
    """Return the calibration classifier from ``expected_path``.

    Contract: the first nonempty line must be exactly one of
    {positive, negative, ambiguous}. Trailing prose comments after
    the classifier are allowed and ignored.

    Raises ``ExpectedClassifierError`` if the file is missing, has
    no nonempty line, or the first nonempty line is not a valid
    classifier token. The prior parser warned-and-proceeded on
    these cases, which silently polluted run_meta.json — see
    REPORT.md Addendum 6.
    """
    if not expected_path.is_file():
        raise ExpectedClassifierError(
            f"expected.txt missing at {expected_path}; cannot determine calibration classifier"
        )
    for raw in expected_path.read_text().splitlines():
        line = raw.strip()
        if not line:
            continue
        if line in VALID_EXPECTED:
            return line
        raise ExpectedClassifierError(
            f"expected.txt at {expected_path} first nonempty line is "
            f"{line!r}; must be exactly one of {VALID_EXPECTED}. "
            "Prose comments are allowed only after the classifier line."
        )
    raise ExpectedClassifierError(f"expected.txt at {expected_path} contains no nonempty line")
