"""Retired calibration runner.

The ``iterate_until_acceptable`` workflow this runner was paired with
has been retired in favour of ``design_loop`` (see T-000016). The
module is intentionally empty; the file remains as a placeholder until
the workspace owner removes it. New calibration work should use
``orchestra.calibration.prji_runner`` or build a design_loop-specific
runner against ``orchestra.run_role``.
"""

from __future__ import annotations
