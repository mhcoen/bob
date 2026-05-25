## Bugs

- [x] T-000001: Ruff UP047 fails on two generic helper functions in `bob_tools/planfile/parser.py`; update the type syntax only, with no behavior change. Current `.venv/bin/ruff check .` output:

```text
UP047 Generic function `_attach_ruledout` should use type parameters
   --> bob_tools/planfile/parser.py:589:5
    |
589 |   def _attach_ruledout(
    |  _____^
590 | |     indent: int,
591 | |     stack: Sequence[_T],
592 | |     root_tasks: Sequence[_T],
593 | | ) -> _T | None:
    | |_^
594 |       """Resolve the task a ``[RULEDOUT]`` line should attach to.
    |
help: Use type parameters

UP047 Generic function `_attach_deps` should use type parameters
   --> bob_tools/planfile/parser.py:613:5
    |
613 |   def _attach_deps(
    |  _____^
614 | |     indent: int,
615 | |     stack: Sequence[_T],
616 | | ) -> tuple[_T | None, bool]:
    | |_^
617 |       """Resolve the task a ``@deps`` sibling line should attach to.
    |
help: Use type parameters

Found 2 errors.
No fixes available (2 hidden fixes can be enabled with the `--unsafe-fixes` option).
```

Offending construct: both helpers are generic functions using the module-level `_T = TypeVar("_T")` style in signatures with `Sequence[_T]` and `_T` return values. Required end state: `.venv/bin/ruff check .` is clean; `.venv/bin/pytest -q` still reports 353 passing tests; `.venv/bin/mypy --strict bob_tools` still passes.
