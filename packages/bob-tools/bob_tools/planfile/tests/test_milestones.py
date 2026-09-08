import pytest

from bob_tools.planfile import PlanValidationError, parse_plan
from bob_tools.planfile.milestones import validate_milestones


def gate(kind="scaffold"):
    return (
        f"- [ ] [AUTO:run_cli] Demonstrate input reaching output [milestone: {kind}] "
        "[demonstrates: transformed input is visible] "
        "[simulated: runtime until phase_002] "
        "[replaces: none] [accept: command-exit: python3 smoke.py]\n"
    )


def test_legacy_plan_is_unchanged_but_new_plan_requires_scaffold():
    plan = parse_plan("## Phase 1: Setup\n- [ ] Compile\n")
    validate_milestones(plan)
    with pytest.raises(PlanValidationError, match="scaffold"):
        validate_milestones(plan, required=True)


def test_scaffold_and_incremental_milestones():
    validate_milestones(
        parse_plan(
            "## Phase 1: Start\n"
            + gate()
            + "## Phase 2: Extend\n"
            + gate("integration")
        ),
        required=True,
    )


@pytest.mark.parametrize(
    "edit",
    [
        lambda s: s.replace("command-exit: python3 smoke.py", "waived: unnecessary"),
        lambda s: s.replace("[simulated: runtime until phase_002]", ""),
        lambda s: s.replace("[AUTO:run_cli]", "[USER]"),
        lambda s: s.replace("milestone: scaffold", "milestone: integration"),
        lambda s: s + "- [ ] Implement after the last milestone\n",
        lambda s: s + "## Phase 2: No integration\n- [ ] Compile\n",
    ],
)
def test_missing_or_nonexecutable_milestones_are_refused(edit):
    with pytest.raises(PlanValidationError):
        validate_milestones(
            parse_plan(edit("## Phase 1: Start\n" + gate())), required=True
        )


def test_adoption_preserves_completed_history_but_cannot_skip_pending_work():
    for status in ("x", " "):
        plan = parse_plan(
            f"## Phase 1: History\n- [{status}] Earlier work\n## Phase 2: Start\n"
            + gate()
        )
        if status == "x":
            validate_milestones(plan)
        else:
            with pytest.raises(PlanValidationError, match="unfinished earlier"):
                validate_milestones(plan)


def test_completed_milestone_cannot_cover_new_pending_work():
    plan = parse_plan(
        "## Phase 1: Scaffold\n- [ ] Newly inserted implementation\n"
        + gate().replace("[ ]", "[x]")
    )
    with pytest.raises(PlanValidationError, match="completed milestone"):
        validate_milestones(plan)
