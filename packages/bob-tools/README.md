# bob-tools

Shared bob-level infrastructure for the Bob toolchain (Duplo, McLoop,
Orchestra, Vroom). Anything that does not belong to one specific tool
but is needed across two or more lives here.

## Current contents

- **`bob_tools.ledger`** — the Plan Ledger. Append-only typed event
  log plus a deterministic projector that turns
  `PLAN.events.jsonl` into `PLAN.state.json`. Captures execution
  evidence and design reasoning so plans can be re-authored from
  the ledger rather than re-derived.

  Design doc: `bob/design/plan-ledger.md` (sibling repo).
  Schema reference: `bob_tools/ledger/SCHEMA.md`.

## Layout

```
bob-tools/
  pyproject.toml         editable install for the bob-tools package
  bob_tools/
    __init__.py
    ledger/
      __init__.py        public re-exports
      events.py          Event/EventType, payload builders
      schema.py          JSON Schema + validator
      _uuid7.py          local UUIDv7 generator (no external dep)
      SCHEMA.md          human-readable schema reference
      tests/             unit tests
```

## Install

```
pip install -e .
```

Adds `bob_tools` to the active Python environment as an editable
package. Consumers (Duplo, McLoop, etc.) can then `import bob_tools`.

## Quality gates

```
pytest                          # 47 passing on Slice A
ruff check bob_tools
mypy --strict bob_tools
```

## License

Copyright 2026 Michael Coen. All rights reserved.
