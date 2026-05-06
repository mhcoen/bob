Refactor buggy_stats.py to add streaming/incremental statistics support while preserving the existing API and tests.

Current state: `compute_running_stats(values: list[float]) -> dict` returns mean, sample variance (N-1), and stddev for a list. All five existing tests in tests/test_stats.py pass.

New requirement: add a `RunningStats` class (or equivalent) that supports incremental updates without re-iterating the whole input. Must expose:
  - `update(value: float) -> None`: incorporate a new sample.
  - `mean` property.
  - `variance` property (sample variance, N-1 denominator).
  - `stddev` property.
  - `n` property (number of samples seen).

Numerical correctness requirement: for any sequence of values, the streaming class's mean/variance/stddev (after the last update) must match the values returned by the existing `compute_running_stats(values)` to within math.isclose tolerance.

Edge cases:
  - n=0: variance and stddev are undefined; reading them must raise ValueError. mean is also undefined; raise ValueError.
  - n=1: variance is undefined for sample variance; raise ValueError on variance/stddev. mean is the single value.
  - n>=2: standard sample-variance formulas.

Do not regress the existing five tests for the list-based API.

Add new tests for the streaming class:
  - test_streaming_matches_batch: verify streaming output equals batch output for [1.0, 2.0, 3.0, 4.0, 5.0].
  - test_streaming_n_zero_raises: variance read on n=0 raises.
  - test_streaming_n_one_raises_variance: variance read on n=1 raises; mean read on n=1 returns the value.

Numerical hint (only if the obvious formula causes precision loss): Welford's online algorithm computes mean and M2 incrementally with low error. Use it if appropriate; otherwise any algorithm whose output passes the tests is acceptable.

Run the full test suite after the refactor to confirm correctness.
