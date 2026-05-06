Audit and fix any defects in buggy_stats.py against its docstring.

The project is at the project_dir directory. There is one Python file, buggy_stats.py, plus a tests/ directory with test_stats.py.

Read buggy_stats.py and tests/test_stats.py carefully. The function compute_running_stats has a docstring that documents specific behavior; the implementation may not match. The current tests pass, but they may pass against the wrong specification.

Your job is to (1) identify any defect where the implementation does not match the docstring, (2) fix the implementation to match the docstring, and (3) update the tests so they validate the documented behavior. The fix should preserve API compatibility and should be minimal — change only what is necessary.

A correct implementation should also handle the singleton-input edge case sensibly (sample variance is undefined for N=1; raise ValueError for that case).

Run the tests after fixing to confirm they pass against the corrected implementation.
