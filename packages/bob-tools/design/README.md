# bob-tools design notes

This directory contains design records for delivered and future
bob-tools work. The table below records the current status and points
from each design note to the implementation that carries it.

| Document | Status | Implementation |
| --- | --- | --- |
| [plan-ledger.md](plan-ledger.md) | Delivered historical baseline | [../bob_tools/ledger/](../bob_tools/ledger/), Duplo Slice C, McLoop Slice D |
| [plan-ledger-slice-b.md](plan-ledger-slice-b.md) | Delivered | [../bob_tools/ledger/thresholds.py](../bob_tools/ledger/thresholds.py) |
| [plan-ledger-slice-c.md](plan-ledger-slice-c.md) | Delivered historical reference | [../../duplo/duplo/reauthor.py](../../duplo/duplo/reauthor.py) |
| [plan-ledger-slice-d.md](plan-ledger-slice-d.md) | Delivered, with multi-runner future path | [../../mcloop/mcloop/ledger_emit.py](../../mcloop/mcloop/ledger_emit.py), [../../mcloop/mcloop/ledger_pause.py](../../mcloop/mcloop/ledger_pause.py), [../../mcloop/mcloop/ledger_config.py](../../mcloop/mcloop/ledger_config.py) |
