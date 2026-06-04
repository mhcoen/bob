<!-- bob-plan-format: 1 -->

# Orchestra

Orchestra controls how multiple LLMs interact. A workflow declares who
drafts, who critiques, who reconciles, who acts; the models, prompts,
and parameters are configuration. Orchestra is embedded as a library
(`orchestra.run_workflow`, `orchestra.run_role`) by other bob packages
(McLoop for per-edit invocations, Duplo for plan authoring) and has a
CLI for direct use.

Python 3.11+. Ruff for linting, pytest for tests. Orchestra must not
depend on consumer packages (no `bob_tools`/`duplo`/`mcloop` imports);
extension points are exposed as caller-supplied hooks so consumers
inject their own behavior without inverting the dependency.

This is Orchestra's first PLAN.md; prior development was done by hand
and is captured in `design/`. New work is authored here and built
through McLoop.

## Phase 1: Role-scoped criteria and caller-supplied transform registration
<!-- phase_id: phase_001 -->

Two extension points are missing that block consumers (Duplo's
iterative plan-authoring loop in particular) from running a workflow
with its own acceptance criteria and its own validation transform.
Both must be additive so existing callers (`run_verb`, Duplo council
at `council.py`, Duplo reauthor at `reauthor.py`) are unaffected.

Extension point A — role-scoped criteria. Today `criteria` is
top-level on `OrchestraConfig` only; `CompoundRoleBinding` has no
criteria field, `run_role` builds its derived config without criteria,
and `_merge_configs` drops top-level criteria on merge. A compound
role cannot carry its own acceptance criteria.

Extension point B — caller-supplied transform registration. Today
`run_workflow` builds both the pre-load and runtime registries
internally and exposes no way for a caller to register a custom
`actor transform`. Built-in transform registration is fixed. A
consumer cannot supply a validation transform (e.g. one that checks an
authored plan body) without dropping below the stable API. The
registration hook must NOT introduce an Orchestra dependency on any
consumer package: Orchestra exposes a callback; the caller supplies a
callback that registers a caller-owned transform.

- [x] T-000001: Add an optional `criteria` field to `CompoundRoleBinding` in `orchestra/config.py` (default empty/None so existing bindings are unchanged). Parse it in `CompoundRoleBinding.from_dict` and reserve the `criteria` key alongside `pattern` and `max_rounds` so it is not swept into `extra`. Use the same criteria shape the top-level `OrchestraConfig.criteria` parser already accepts, reusing that parsing path rather than duplicating it. Unit tests in the orchestra config tests: a compound binding with criteria round-trips through `from_dict`; a binding without criteria still parses and carries empty/None criteria; the `criteria` key no longer appears in `extra`. <!-- created_at: 2026-06-03T00:00:00Z --> <!-- completed_at: 2026-06-04T04:32:07Z -->
- [x] T-000002: Fix `_merge_configs` in `orchestra/config.py` so it preserves and merges top-level `criteria` instead of reconstructing `OrchestraConfig` without it. Define the merge rule explicitly (later config's criteria override earlier per-criterion by id; non-overlapping criteria union) and document it in the function docstring. Unit tests: merging two configs each with distinct criteria yields the union; merging where both define the same criterion id takes the later; merging a config that has criteria with one that does not preserves the criteria. This corrects a current latent bug (criteria silently dropped on merge), independent of the rest of this phase. <!-- created_at: 2026-06-03T00:00:00Z --> <!-- completed_at: 2026-06-04T04:33:28Z -->
- [x] T-000003: In `run_role` (`orchestra/api/dispatch.py`), populate the derived `OrchestraConfig.criteria` from the resolved compound binding's criteria, falling back to the merged top-level criteria when the binding declares none, so a role-scoped criteria set reaches the executor. `run_workflow` already forwards `cfg.criteria` to the `Executor`, so no change is needed there; confirm that path with a test. Unit tests: `run_role` against a compound binding with criteria runs the executor with those criteria (assert via a stubbed executor or the criteria-mode observable); a binding without criteria falls back to top-level; neither path regresses the existing `design` role. <!-- created_at: 2026-06-03T00:00:00Z --> <!-- completed_at: 2026-06-04T04:36:33Z -->
- [x] T-000004: Add an optional caller-supplied transform-registration hook to `run_workflow` (`orchestra/api/dispatch.py`) — e.g. a `registry_customizer` callable parameter (default None) that is invoked on BOTH the pre-load registry and the runtime registry after core registration, so a caller can register an `actor transform` the workflow loader (which validates transforms are registered before load) and the executor both see. The parameter must be optional and additive; all existing `run_workflow` callers continue to work unchanged. Orchestra must not import any consumer package to support this; the transform implementation is owned and supplied by the caller. Unit tests: a workflow referencing a caller-registered transform loads and runs; without the customizer the same workflow fails to load (transform unregistered) exactly as today; existing callers with no customizer are unaffected. <!-- created_at: 2026-06-03T00:00:00Z --> <!-- completed_at: 2026-06-04T04:40:14Z -->
- [x] T-000005: Thread the same `registry_customizer` parameter through `run_role` (`orchestra/api/dispatch.py`) to `run_workflow`, optional and additive, so role-dispatched workflows can also register a caller-owned transform. Unit tests: `run_role` with a customizer registers the transform for a role-bound workflow that references it; `run_role` without a customizer is unchanged; the customizer is applied to both registries on the role path. <!-- created_at: 2026-06-03T00:00:00Z --> <!-- completed_at: 2026-06-04T04:43:38Z -->
- [x] T-000006: Run the full orchestra test suite and confirm the phase closes cleanly: existing `run_workflow`/`run_role`/`run_verb` callers and the `design`/council workflows are unaffected, the criteria-merge tests pass, and the two new extension points work end to end. If any test fails, the task fails. <!-- created_at: 2026-06-03T00:00:00Z --> <!-- completed_at: 2026-06-04T05:56:39Z -->
