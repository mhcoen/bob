# Migration log

Chronological record of cross-cutting migrations affecting bob-tools.
Active design lives in [../design/](../design/); this file records
execution history after the design has landed.

## 2026-05-21 -- Duplo reauthor migrated from `duplo.plan_document` to `bob_tools.planfile`

Duplo's private `duplo.plan_document` model used to own PLAN.md parsing,
phase assembly, and plan-artifact cleanup for the reauthor path. That
responsibility moved to `bob_tools.planfile`, specifically:

- [../bob_tools/planfile/parser.py](../bob_tools/planfile/parser.py) for PLAN.md parsing.
- [../bob_tools/planfile/model.py](../bob_tools/planfile/model.py) for the typed plan model.
- [../bob_tools/planfile/operations.py](../bob_tools/planfile/operations.py) for canonical validation and phase replacement.
- [../bob_tools/planfile/plan_artifact.py](../bob_tools/planfile/plan_artifact.py) for plan-artifact sanitizing.

Duplo was updated to consume the shared library:

- [../../duplo/duplo/reauthor.py](../../duplo/duplo/reauthor.py) parses, validates, sanitizes, and saves through `bob_tools.planfile`.
- [../../duplo/duplo/reauthor_assemble.py](../../duplo/duplo/reauthor_assemble.py) handles constructed-mode phase assembly around `bob_tools.planfile` primitives.

Source tasks in this repo: `PLAN.md` T-000192 through T-000197.

Follow-up: `duplo/plan_document.py` and its tests were deleted in Duplo
commit `f047808` on 2026-05-24.

Primary historical record: `NOTES.md` entries `[21.1]` through `[23.2]`.
Those entries preserve the migration evidence as it was observed at
the time; later supersession notes should point here rather than
rewriting the original record.
