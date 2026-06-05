"""Tests for the per-file ``task_namespace`` extension (T-000003).

The grammar admits an optional two-letter namespace declared once in
the preamble as ``<!-- task_namespace: XX -->`` and used to prefix
canonical task ids in ``T-XX-NNNNNN`` form. Legacy ``T-NNNNNN`` ids
continue to parse; the canonical validator warns once per file when a
namespaced plan still carries unprefixed ids.
"""

from __future__ import annotations

import warnings

from bob_tools.planfile import (
    PlanValidationError,
    assert_mcloop_canonical,
    canonicalize,
    parse_plan,
    render_plan,
    validate_plan,
)
from bob_tools.planfile.operations import _next_task_id


def _plan_with_namespace(ns: str = "AB") -> str:
    return (
        "<!-- bob-plan-format: 1 -->\n"
        "\n"
        f"<!-- task_namespace: {ns} -->\n"
        "\n"
        "# Namespaced Plan\n"
        "\n"
        "## Stage 1: Bootstrap\n"
        "<!-- phase_id: phase_001 -->\n"
        "\n"
        f"- [ ] T-{ns}-000001: parent\n"
        f"  - [ ] T-{ns}-000002: child a [accept: pytest]\n"
        f"- [x] T-{ns}-000003: done thing [accept: pytest]\n"
    )


class TestNamespaceParse:
    def test_preamble_namespace_captured(self) -> None:
        plan = parse_plan(_plan_with_namespace("AB"))
        assert plan.task_namespace == "AB"

    def test_absent_namespace_is_none(self) -> None:
        text = "# Plain Plan\n\n## Stage 1: Bootstrap\n\n- [ ] T-000001: task\n"
        plan = parse_plan(text)
        assert plan.task_namespace is None

    def test_namespaced_task_ids_round_trip(self) -> None:
        plan = parse_plan(_plan_with_namespace("XY"))
        ids = [
            task.task_id
            for phase in plan.phases
            for root in phase.tasks
            for task in (root, *root.children)
        ]
        assert ids == ["T-XY-000001", "T-XY-000002", "T-XY-000003"]

    def test_namespace_lowercase_accepted(self) -> None:
        # The grammar admits ``[A-Za-z]{2}``; lowercase is preserved so
        # round-trip is byte-stable. The structural-sanity check does
        # not normalize case.
        plan = parse_plan(_plan_with_namespace("ab"))
        assert plan.task_namespace == "ab"

    def test_mixed_namespaced_and_legacy_ids_parse(self) -> None:
        text = (
            "<!-- bob-plan-format: 1 -->\n"
            "\n"
            "<!-- task_namespace: AB -->\n"
            "\n"
            "# Mixed Plan\n"
            "\n"
            "## Stage 1: Bootstrap\n"
            "<!-- phase_id: phase_001 -->\n"
            "\n"
            "- [ ] T-AB-000001: namespaced task\n"
            "- [ ] T-000002: legacy unprefixed task\n"
        )
        plan = parse_plan(text)
        ids = [task.task_id for phase in plan.phases for task in phase.tasks]
        assert ids == ["T-AB-000001", "T-000002"]


class TestNamespaceRoundTrip:
    def test_render_emits_namespace_comment(self) -> None:
        plan = parse_plan(_plan_with_namespace("AB"))
        rendered = render_plan(plan)
        assert "<!-- task_namespace: AB -->" in rendered

    def test_canonicalize_idempotent_with_namespace(self) -> None:
        text = _plan_with_namespace("AB")
        once = canonicalize(text)
        twice = canonicalize(once)
        assert once == twice

    def test_render_skips_comment_when_no_namespace(self) -> None:
        # A plan with task_namespace=None must not emit the comment.
        text = "# Plain\n\n## Stage 1: Bootstrap\n\n- [ ] T-000001: task\n"
        rendered = render_plan(parse_plan(text))
        assert "task_namespace" not in rendered


class TestNamespacedNextId:
    def test_next_id_uses_namespace_prefix(self) -> None:
        plan = parse_plan(_plan_with_namespace("AB"))
        assert _next_task_id(plan) == "T-AB-000004"

    def test_next_id_legacy_when_no_namespace(self) -> None:
        text = "# Plain\n\n## Stage 1: Bootstrap\n\n- [ ] T-000005: task\n"
        plan = parse_plan(text)
        assert _next_task_id(plan) == "T-000006"

    def test_next_id_advances_past_mixed_ids(self) -> None:
        # Counter is shared across legacy + namespaced ids: a plan with
        # ``T-000050`` and ``T-AB-000007`` must allocate ``T-AB-000051``
        # so the new id cannot collide with the legacy one.
        text = (
            "<!-- bob-plan-format: 1 -->\n"
            "\n"
            "<!-- task_namespace: AB -->\n"
            "\n"
            "# Mixed\n"
            "\n"
            "## Stage 1: Bootstrap\n"
            "<!-- phase_id: phase_001 -->\n"
            "\n"
            "- [ ] T-000050: legacy\n"
            "- [ ] T-AB-000007: namespaced\n"
        )
        plan = parse_plan(text)
        assert _next_task_id(plan) == "T-AB-000051"


class TestCanonicalValidatorWarns:
    def test_namespaced_plan_with_legacy_ids_warns_once(self) -> None:
        text = (
            "<!-- bob-plan-format: 1 -->\n"
            "\n"
            "<!-- task_namespace: AB -->\n"
            "\n"
            "# Mixed\n"
            "\n"
            "## Stage 1: Bootstrap\n"
            "<!-- phase_id: phase_001 -->\n"
            "\n"
            "- [ ] T-AB-000001: namespaced [accept: pytest]\n"
            "- [ ] T-000002: legacy [accept: pytest]\n"
            "- [ ] T-000003: also legacy [accept: pytest]\n"
        )
        plan = parse_plan(text)
        validate_plan(plan, constructed=True)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert_mcloop_canonical(plan)
        ns_warnings = [w for w in caught if "task_namespace" in str(w.message)]
        assert len(ns_warnings) == 1
        assert "T-AB-NNNNNN" in str(ns_warnings[0].message)

    def test_namespaced_plan_with_only_namespaced_ids_does_not_warn(self) -> None:
        plan = parse_plan(_plan_with_namespace("AB"))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert_mcloop_canonical(plan)
        ns_warnings = [w for w in caught if "task_namespace" in str(w.message)]
        assert ns_warnings == []

    def test_unnamespaced_plan_does_not_warn(self) -> None:
        # The warning is gated on a declared namespace; legacy files
        # that never opted in stay quiet.
        text = (
            "<!-- bob-plan-format: 1 -->\n"
            "\n"
            "# Legacy\n"
            "\n"
            "## Stage 1: Bootstrap\n"
            "<!-- phase_id: phase_001 -->\n"
            "\n"
            "- [ ] T-000001: legacy task\n"
        )
        plan = parse_plan(text)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert_mcloop_canonical(plan)
        ns_warnings = [w for w in caught if "task_namespace" in str(w.message)]
        assert ns_warnings == []


class TestNamespaceValidation:
    def test_namespaced_id_passes_dep_validation(self) -> None:
        text = (
            "<!-- bob-plan-format: 1 -->\n"
            "\n"
            "<!-- task_namespace: AB -->\n"
            "\n"
            "# Deps\n"
            "\n"
            "## Stage 1: Bootstrap\n"
            "<!-- phase_id: phase_001 -->\n"
            "\n"
            "- [ ] T-AB-000001: leader\n"
            "- [ ] T-AB-000002: follower\n"
            "  @deps T-AB-000001\n"
        )
        plan = parse_plan(text)
        # Should not raise.
        validate_plan(plan)

    def test_unknown_namespaced_dep_is_rejected(self) -> None:
        text = (
            "<!-- bob-plan-format: 1 -->\n"
            "\n"
            "<!-- task_namespace: AB -->\n"
            "\n"
            "# Deps\n"
            "\n"
            "## Stage 1: Bootstrap\n"
            "<!-- phase_id: phase_001 -->\n"
            "\n"
            "- [ ] T-AB-000001: only one task\n"
            "  @deps T-AB-000099\n"
        )
        plan = parse_plan(text)
        try:
            validate_plan(plan)
        except PlanValidationError as exc:
            assert any("T-AB-000099" in m for m in exc.messages)
        else:  # pragma: no cover - guard
            raise AssertionError("expected PlanValidationError")
