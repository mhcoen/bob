"""Generate PLAN.md files for building phases of an application."""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from bob_tools.planfile import (
    Phase,
    Plan,
    PlanSyntaxError,
    PlanValidationError,
    Task,
    add_phase_task,
    make_task,
    parse_plan,
)
from bob_tools.planfile import load as planfile_load
from bob_tools.planfile import migrate as planfile_migrate
from bob_tools.planfile import save as planfile_save

from duplo import council
from duplo.claude_cli import query
from duplo.extractor import Feature
from duplo.plan_author_adapter import run_plan_author
from duplo.questioner import BuildPreferences
from duplo.reauthor_phase_ids import stamp_sequential_phase_ids

_PHASE_SYSTEM = """\
You are a senior software architect generating a build plan for one
phase of an application.

You will be given a roadmap phase (number, title, goal, features,
test criteria) along with build preferences. Generate a phase body
that McLoop can execute. McLoop works through checklist items one
at a time, launching a fresh Claude Code session per task.

Rules for the plan:
- Each checklist item should be a single, focused unit of work.
- Items should be ordered so each leaves the project in a building
  and runnable state.
- Phase 0 (scaffold) should create the project structure, build
  system, and a minimal window or entry point. Nothing else.
- Later phases should build incrementally on existing code.
- Aim for 5-15 checklist items per phase.
- Use subtasks (indented items) for complex items.
- When a parent task has multiple subtasks that are all specific
  enough to be executed without design decisions (file paths,
  function names, explicit conditionals, concrete values), mark
  the parent with [BATCH] so McLoop combines them into a single
  session. Do NOT use [BATCH] on tasks whose subtasks require
  significant design decisions or architectural exploration.
  Do NOT use [BATCH] if any subtask is marked [USER] or [AUTO];
  McLoop handles this automatically by stopping the batch at
  those boundaries, but the intent should be clear in the plan.
- Reserve [USER] only for genuinely human-only checks with no
  scriptable form, such as visual or physical confirmation. A
  runnable verification command, test, or script must never be a
  [USER] task. For scriptable verification, first add a normal task
  to create a helper script that hardcodes paths, takes no args,
  prints progress, and exits non-zero on failure; then add a
  [AUTO:run_cli] task that invokes that helper with an absolute
  command path. McLoop will pause only on true [USER] tasks and
  wait for the human to perform and confirm them.
- Do NOT include a platform, language, prerequisites, or
  build-system description paragraph at the top of the phase.
  That information is written once in the PLAN.md project
  header and must not be repeated per phase. Start the phase
  content with the canonical Slice C ``## Phase phase_NNN:``
  header line, then go directly to task checkboxes.
- Do NOT emit a separate visual-design section. Any design
  requirements are injected into PLAN.md by the caller, after
  the phase heading.
- If known issues are provided, generate fix tasks for each one.
  Order fix tasks before new feature work when a feature depends
  on the fix (e.g. a broken API must be fixed before building a
  feature that calls it). Fixes that are independent of upcoming
  features can be placed wherever they fit best.
- Every task line that implements one or more features from the
  input list MUST end with a [feat: "Feature Name"] annotation.
  If a task addresses multiple features, list them comma-separated:
  [feat: "Push-to-talk recording", "Global keyboard shortcuts"].
  Tasks that fix bugs or issues use [fix: "description"] instead.
  Scaffolding or structural tasks that do not map to any feature
  use no annotation.

Canonical Slice C form (the runtime owns phase identity):

## Phase phase_NNN: <Title>

- [ ] Set up project structure and build system
- [ ] [BATCH] Add user authentication [feat: "User authentication"]
  - [ ] Create `AuthService.swift` with `login(email:password:)` and `signup(email:password:)` methods
  - [ ] Add `LoginView.swift` with email/password fields and submit button
  - [ ] Wire `AuthService` into the app lifecycle, store session token in Keychain
- [ ] Fix input validation on signup [fix: "email format not checked"]
- [ ] ...

The H2 ``## Phase phase_NNN:`` header MUST use the exact phase_id
the runtime supplied; do not invent your own ordinal. Do NOT
emit a top-level ``# <AppName> — Phase N: <Title>`` H1; the
runtime owns the project envelope.
"""

_NEXT_PHASE_SYSTEM = """\
You are a senior software architect helping to plan the next phase of an application build.

Given the completed phase plan, user feedback, and (optionally) visual issues from
screenshot comparison, produce the next phase body. The next phase must:
- Build incrementally on what was completed in the previous phase
- Address all user feedback items
- Fix any visual issues identified in screenshot comparison
- Add the next most valuable batch of features (not everything at once)

Emit the canonical Slice C phase body: a ``## Phase phase_NNN: <title>``
H2 header followed by ``- [ ]`` checklist tasks. The runtime owns the
phase_id and the project H1 envelope; do not invent ordinals or wrap
the body in a ``# <AppName>`` heading.

## Phase phase_NNN: <short title>

- [ ] Implementation step. Steps that implement one or more features
  MUST end with a [feat: "Feature Name"] annotation. Multiple
  features go comma-separated: [feat: "Push-to-talk recording",
  "Global keyboard shortcuts"]. Bug or visual-issue fixes use
  [fix: "description"]. Scaffolding or structural steps that do
  not map to any feature use no annotation.

Additional rules:
- When a step has multiple subtasks that are all specific enough
  to be executed without design decisions, mark the parent step
  with [BATCH] so McLoop combines the subtasks into a single
  session for efficiency.
- Reserve [USER] only for genuinely human-only checks with no
  scriptable form. Runnable verification must be expressed as a
  helper-script creation step plus an [AUTO:run_cli] step that
  invokes the helper with an absolute command path.
"""

_PLAN_FILENAME = "PLAN.md"

_FENCE_RE = re.compile(
    r"\A\s*(?:```|~~~)[\w]*\s*\n(.*?)\n\s*(?:```|~~~)\s*\Z",
    re.DOTALL,
)

_H1_HEADING_RE = re.compile(r"^# \S")

# Any markdown heading (any depth) followed by content. Used as the
# preamble boundary in _ensure_h1_heading: lines before the first
# heading are LLM commentary or separator noise (`Here is the plan:`,
# `---`); lines from the first heading onward are real content.
# Slice C's inner `## Phase phase_NNN:` qualifies as a heading and
# anchors content even when it sits above a stray H1.
_ANY_HEADING_RE = re.compile(r"^#+\s+\S")

# Strip and validate use SEPARATE regex constants. Codex's framing:
# "The symmetry is a concern, not a virtue here. Strip and validation
# have different jobs. Sharing the same permissive regex means both
# can agree on the wrong interpretation."
#
# Strip pattern: superset of mcloop's checklist.py STAGE_RE
# (^#+\s+.*?\b(?:stage|phase)\s+(\d+)\b, IGNORECASE). Whatever mcloop
# would parse as a phase/stage header, Duplo MUST also recognize and
# remove. False positives are fine; false negatives leave duplicate
# headings that mcloop sees but Duplo missed, which fail mcloop's
# duplicate-Phase/Stage check at parse time.
#
# Catches every shape mcloop matches:
#   - any heading level (^#+), not just H1 (catches ## Phase 3, ### Stage 5)
#   - "Phase N" or "Stage N" anywhere in the heading text
#   - no colon required after the digit (catches `# Phase 3 Glob filtering`)
#   - lowercase, uppercase, or mixed-case (re.IGNORECASE)
#   - any separator before the keyword: em-dash, en-dash, hyphen-minus,
#     or none
#
# Critically does NOT catch the inner Slice C semantic header
# `## Phase phase_NNN: title`: the `phase_001` token has no
# whitespace between "phase" and the digit (the underscore breaks
# the `phase\s+\d+` pattern), so Slice C headers survive the strip.
_PHASE_H1_STRIP_RE = re.compile(r"^#+\s+.*?\b(?:stage|phase)\s+(\d+)\b", re.IGNORECASE)

# Validate pattern: STRICT match of the canonical envelope shape Duplo
# renders. This is the regex used by validate_h1_ordinal_sequence to
# extract ordinals from PLAN.md for the source-of-truth check. False
# positives here would let prose H1s like
# `# Background: Phase 1 introduced filtering` be counted as phase
# ordinals, breaking validation against actual roadmap state.
#
# Matches exactly what _ensure_h1_heading prepends: single-hash, em-dash
# separator, "Phase " (capitalized), digit, colon-space, trailing title.
_PHASE_H1_VALIDATE_RE = re.compile(r"^# .+? — Phase (\d+): .+$")


def _strip_fences(text: str) -> str:
    """Remove outer triple-backtick fences if the LLM wrapped the plan."""
    m = _FENCE_RE.match(text)
    return m.group(1) if m else text


def _strip_trailing_commentary(content: str) -> str:
    """Truncate *content* after the last ``- [ ]`` task or subtask line.

    Finds the last line whose leading-whitespace-stripped prefix is ``- [ ]``
    (a task or subtask checkbox) and discards everything after it, leaving
    exactly one trailing newline. This handles the case where the LLM wraps
    the plan in code fences AND adds meta-commentary after the closing fence:
    ``_strip_fences`` cannot remove such commentary because ``_FENCE_RE``
    requires the closing fence at end-of-string. The correct invariant is
    that nothing should appear in phase content after the last task.

    If no task line is found, or the last task line is already the final
    line of *content*, returns *content* unchanged -- there is no trailing
    commentary to strip and we preserve the exact input formatting
    (including any absence of a trailing newline, which the caller may
    rely on).
    """
    lines = content.splitlines()
    last_task_idx: int | None = None
    for i, line in enumerate(lines):
        if line.lstrip().startswith("- [ ]"):
            last_task_idx = i
    if last_task_idx is None or last_task_idx == len(lines) - 1:
        return content
    kept = lines[: last_task_idx + 1]
    return "\n".join(kept) + "\n"


def _ensure_h1_heading(
    content: str,
    project_name: str,
    phase_num: int,
    phase_title: str,
) -> str:
    """Strip-and-render the H1 phase heading.

    Phase ordinal in the outer ``# <project_name> — Phase N: <title>``
    H1 is execution metadata that belongs to Duplo's roadmap state,
    not to the synthesizer. Earlier versions of this function trusted
    whatever H1 the synthesizer wrote (or prepended one only when
    none was present). That kept the synthesizer in the ownership
    loop for phase ordinals: parallel council invocations have no
    shared counter, the synthesizer guesses the ordinal, gets it
    wrong (duplicates, gaps, off-by-one), the resulting PLAN.md
    fails mcloop's parser.

    The durable rule (codex's framing): model emits phase content;
    Duplo wraps it in the deterministic PLAN.md envelope. So this
    function:

      1. Strips any leading non-H1 commentary (preambles like "Here
         is the plan:" or stray "---" separators that some models
         emit above the heading).
      2. Strips ALL model-authored ``# <something> — Phase N: ...``
         H1 lines from the body. The synthesizer may have written
         one, several, or none; all are removed.
      3. Renders the canonical H1 from Duplo's roadmap state
         (``project_name``, ``phase_num``, ``phase_title``) and
         prepends it to the cleaned body.

    Even if the synthesizer ignores its template instructions and
    fabricates an H1, the strip-and-render step overwrites it.
    Model-authored phase ordinals cannot escape Duplo's control.
    """
    lines = content.splitlines(keepends=True)

    # Step 1: strip leading non-heading commentary up to the first
    # markdown heading of ANY depth. The boundary is "first heading"
    # rather than "first H1" because the synthesizer's body, under
    # the new contract, contains a Slice C `## Phase phase_NNN:` H2
    # as its first heading. Treating any-depth heading as the
    # boundary keeps that H2 as content while still stripping
    # LLM commentary preambles (`Here is the plan:`, `---`).
    first_heading_idx: int | None = None
    for i, line in enumerate(lines):
        if _ANY_HEADING_RE.match(line):
            first_heading_idx = i
            break
    if first_heading_idx is not None:
        lines = lines[first_heading_idx:]

    # Step 2: drop every phase/stage heading mcloop would parse as a
    # section header (any heading level containing "Phase N" or
    # "Stage N", with or without colon, in any case). The strip
    # regex is a superset of mcloop's checklist.py STAGE_RE.
    cleaned: list[str] = []
    for line in lines:
        if _PHASE_H1_STRIP_RE.match(line.rstrip("\n")):
            continue
        cleaned.append(line)

    body = "".join(cleaned).lstrip()

    # Step 3: render the canonical H1 from Duplo's roadmap state.
    app_name = project_name or "App"
    heading = f"# {app_name} — Phase {phase_num}: {phase_title}"
    if body:
        return f"{heading}\n\n{body}"
    return f"{heading}\n"


class CanonicalH1OrdinalError(RuntimeError):
    """Raised when PLAN.md's H1 phase ordinals violate the expected sequence."""


def validate_h1_ordinal_sequence(
    plan_text: str,
    expected_ordinals: list[int] | None = None,
) -> None:
    """Validate H1 phase ordinals against the source-of-truth.

    Extracts every canonical-envelope H1 line (strict match against
    ``_PHASE_H1_VALIDATE_RE``: single-hash, em-dash separator, "Phase ",
    digit, colon-space, trailing title) in document order. Validation
    has two modes:

    1. ``expected_ordinals`` is provided: the observed sequence MUST
       equal it exactly. This is the source-of-truth check Duplo's
       roadmap state can drive — pipeline accumulates the list of
       ordinals it has emitted across save_plan calls and passes it
       in. Catches any drift (duplicate, gap, out-of-order, wrong
       starting ordinal, missing phase, extra phase) in a single
       comparison.

    2. ``expected_ordinals`` is None: the observed sequence MUST be
       contiguous and monotonic ``[K, K+1, ..., K+N-1]`` for some
       non-negative starting K. Backward-compatible fallback for
       callers that don't yet have roadmap state to drive the
       check.

    The strict extraction regex deliberately excludes prose H1s like
    ``# Background: Phase 1 introduced filtering``: those are content,
    not envelope. Strip-time treats them as false-positive noise to
    remove; validation-time treats them as not-a-phase-heading.

    Raises ``CanonicalH1OrdinalError`` naming the observed sequence
    and the expected sequence on any mismatch. Returns silently when
    the sequence is valid OR when no canonical H1 phase headings are
    present (the validator is no-op for plans that have not yet
    received their first canonical H1).
    """
    ordinals: list[int] = []
    for raw_line in plan_text.splitlines():
        match = _PHASE_H1_VALIDATE_RE.match(raw_line)
        if match:
            ordinals.append(int(match.group(1)))
    if not ordinals:
        return

    if expected_ordinals is not None:
        if ordinals == expected_ordinals:
            return
        raise CanonicalH1OrdinalError(
            "PLAN.md H1 phase ordinal sequence does not match the "
            "source-of-truth from Duplo's roadmap state. "
            f"Observed: {ordinals}. Expected: {expected_ordinals}."
        )

    expected_contig = list(range(ordinals[0], ordinals[0] + len(ordinals)))
    if ordinals == expected_contig:
        return
    raise CanonicalH1OrdinalError(
        "PLAN.md H1 phase ordinal sequence is not contiguous and "
        f"monotonic. Observed: {ordinals}. Expected: {expected_contig}."
    )


@dataclasses.dataclass
class CompletedTask:
    """A checked task line parsed from PLAN.md."""

    text: str
    features: list[str] = dataclasses.field(default_factory=list)
    fixes: list[str] = dataclasses.field(default_factory=list)
    indent: int = 0


def parse_completed_tasks(plan_content: str) -> list[CompletedTask]:
    """Parse checked task lines from PLAN.md content.

    Finds all ``- [x]`` (case-insensitive) lines and extracts:
    - The task description text (without the checkbox prefix and annotation suffix)
    - Any ``[feat: "..."]`` feature annotations
    - Any ``[fix: "..."]`` fix annotations
    - The indentation level (number of leading spaces)

    Args:
        plan_content: Full Markdown content of PLAN.md.

    Returns:
        List of :class:`CompletedTask` for each checked line, in order.
    """
    tasks: list[CompletedTask] = []
    for line in plan_content.splitlines():
        stripped = line.lstrip()
        if not (stripped.startswith("- [x]") or stripped.startswith("- [X]")):
            continue
        indent = len(line) - len(stripped)
        # Remove the checkbox prefix.
        body = stripped[5:].strip()
        # Extract trailing annotations (one or more [feat:]/[fix:] at end).
        features: list[str] = []
        fixes: list[str] = []
        trailing = re.search(
            r"(\s*\[(feat|fix):\s*\"[^\"]+\"(?:,\s*\"[^\"]+\")*\])+\s*$",
            body,
        )
        if trailing:
            tail = body[trailing.start() :]
            for anno_match in re.finditer(
                r"\[(feat|fix):\s*(\"[^\"]+\"(?:,\s*\"[^\"]+\")*)\]",
                tail,
            ):
                kind = anno_match.group(1)
                raw_names = re.findall(r"\"([^\"]+)\"", anno_match.group(2))
                if kind == "feat":
                    features.extend(raw_names)
                else:
                    fixes.extend(raw_names)
            body = body[: trailing.start()].rstrip()
        tasks.append(
            CompletedTask(
                text=body,
                features=features,
                fixes=fixes,
                indent=indent,
            )
        )
    return tasks


def _detect_next_phase_number(current_plan: str) -> int:
    """Return the next phase number inferred from *current_plan* heading."""
    match = re.search(r"#\s*.*?(?:Phase|Stage)\s+(\d+)", current_plan, re.IGNORECASE)
    return (int(match.group(1)) + 1) if match else 2


def generate_next_phase_plan(
    current_plan: str,
    feedback: str,
    issues_text: str = "",
    *,
    platform_addendum: str = "",
) -> str:
    """Return the next phase PLAN.md content as a string.

    Uses ``claude -p`` to generate the plan based on the completed phase
    plan, user feedback, and visual issues from screenshot comparison.

    Args:
        current_plan: Markdown content of the just-completed PLAN.md.
        feedback: User feedback collected after testing the phase.
        issues_text: Optional visual issues text (e.g. from ISSUES.md).
        platform_addendum: Optional platform-rules text appended to the
            system prompt when non-empty.

    Returns:
        Markdown string suitable for writing to ``PLAN.md``.
    """
    next_phase = _detect_next_phase_number(current_plan)

    issues_section = (
        f"\nVisual issues identified in screenshots:\n{issues_text.strip()}\n"
        if issues_text.strip()
        else "\nNo visual issues reported.\n"
    )

    prompt = f"""\
Completed phase plan:
{current_plan.strip()}

User feedback:
{feedback.strip()}
{issues_section}
Generate Phase {next_phase} PLAN.md now.
"""

    system = _NEXT_PHASE_SYSTEM + platform_addendum if platform_addendum else _NEXT_PHASE_SYSTEM
    return _strip_fences(query(prompt, system=system))


def generate_phase_plan(
    source_url: str,
    features: list[Feature],
    preferences: BuildPreferences,
    phase: dict | None = None,
    *,
    project_name: str = "",
    phase_number: int | None = None,
    spec_text: str = "",
    platform_addendum: str = "",
    prior_phases_files: list[str] | None = None,
    target_dir: Path | str = ".",
    escalate_to_council: bool = False,
) -> Plan:
    """Generate a typed :class:`Plan` for a specific roadmap phase.

    Returns a validated :class:`bob_tools.planfile.Plan` that:

    1. By default (unconditional), comes from the iterative authoring
       adapter :func:`duplo.plan_author_adapter.run_plan_author`, which
       drives the duplo-owned ``plan_author`` role through Orchestra's
       validation-gated loop and returns a canonical-Slice-C body. That
       body flows through the unchanged
       :func:`duplo.council.typed_plan_from_synthesizer_text` ->
       :func:`save_plan` tail (the converged body is parsed, rebuilt as a
       constructed plan, ids assigned, and validated under both
       ``validate_plan(constructed=True)`` and ``assert_mcloop_canonical``).
       A non-converging (``CAPPED``) run raises and produces no plan, so
       PLAN.md is never written with an unvalidated body.
    2. Only when ``escalate_to_council`` is explicitly set does plan
       authoring route to :func:`duplo.council.author_phase_plan` (the
       council_four fan-out). Council is an opt-in escalation/experiment
       path, not part of normal authoring; the ``DUPLO_USE_COUNCIL`` env
       var / :func:`duplo.council.is_enabled` is no longer consulted by
       this function.

    No raw markdown leaks past this boundary; the caller must persist
    via :func:`save_plan`, which delegates to
    :func:`bob_tools.planfile.save`.

    Args:
        source_url: The product URL that was scraped.
        features: All selected features.
        preferences: Build preferences.
        phase: A roadmap phase dict with phase, title, goal,
            features, and test. If None, generates a generic
            Phase 1 plan.
        project_name: Name for the project.
        phase_number: Override for the phase number in the heading.
            When provided, this is used instead of ``phase["phase"]``.
            Derived from the length of the ``phases`` history + 1.
        platform_addendum: Optional platform-rules text appended to the
            system prompt when non-empty.
        prior_phases_files: Filenames (paths) already produced by earlier
            phases in this run. When non-empty, the prompt instructs the
            LLM not to recreate or redefine these files so the next phase
            builds on prior output instead of duplicating it.
        target_dir: Directory whose PLAN.md drives the deterministic
            ``required_phase_id`` for the default iterative-authoring
            path. Ignored by the council path (which computes the same
            value internally).
        escalate_to_council: Explicit opt-in escalation. When True, route
            authoring to :func:`duplo.council.author_phase_plan` instead
            of the default iterative adapter. Defaults to False so
            iterative authoring is the unconditional default.
    """
    prefs_dict = dataclasses.asdict(preferences)
    constraints_text = (
        "\n".join(f"  - {c}" for c in prefs_dict["constraints"])
        if prefs_dict["constraints"]
        else "  (none)"
    )
    preferences_text = (
        "\n".join(f"  - {p}" for p in prefs_dict["preferences"])
        if prefs_dict["preferences"]
        else "  (none)"
    )

    if phase:
        phase_num = phase_number if phase_number is not None else phase["phase"]
        phase_title = phase["title"]
        phase_goal = phase["goal"]
        phase_features = phase.get("features", [])
        phase_test = phase.get("test", "")
        phase_issues = phase.get("issues", [])
        features_text = "\n".join(f"- {name}" for name in phase_features) or "(scaffold only)"
    else:
        phase_num = phase_number if phase_number is not None else 1
        phase_title = "Core"
        phase_goal = "Smallest end-to-end working thing"
        features_text = "\n".join(f"- {f.name}: {f.description}" for f in features)
        phase_test = ""
        phase_issues = []

    issues_block = ""
    if phase_issues:
        issues_text = "\n".join(f"- {desc}" for desc in phase_issues)
        issues_block = f"\nKnown issues to fix in this phase:\n{issues_text}\n"

    spec_block = ""
    if spec_text:
        spec_block = f"\nProduct specification (authoritative, from the user):\n{spec_text}\n"

    prior_files_block = ""
    if prior_phases_files:
        prior_files_list = "\n".join(f"- {name}" for name in prior_phases_files)
        prior_files_block = (
            "\nFiles already created in earlier phases -- do NOT recreate or redefine these:\n"
            f"{prior_files_list}\n"
        )

    # The canonical phase_id is computed deterministically by the runtime
    # (1-indexed: the first phase is phase_001), independent of the
    # human-facing 0-indexed roadmap "Phase N" label. Compute it BEFORE
    # building the prompt and instruct the synthesizer to emit it verbatim
    # so its header matches what typed_plan_from_synthesizer_text demands.
    plan_path = Path(target_dir) / _PLAN_FILENAME
    required_phase_id = council.compute_required_phase_id(plan_path)
    phase_id_block = (
        "\nPhase header: use this exact id verbatim and do NOT renumber it.\n"
        "The runtime assigns the phase_id; the validator rejects any other.\n"
        f"The first line of the phase body must be:\n## Phase {required_phase_id}: {phase_title}\n"
    )

    prompt = f"""\
Project: {project_name or source_url}
Source: {source_url}

Phase {phase_num}: {phase_title}
Goal: {phase_goal}
Test: {phase_test}

Platform: {prefs_dict["platform"]}
Language/stack: {prefs_dict["language"]}
Constraints:
{constraints_text}
Preferences:
{preferences_text}

Features for this phase:
{features_text}
{issues_block}{spec_block}{prior_files_block}{phase_id_block}
Generate the phase body now.
"""

    system = _PHASE_SYSTEM + platform_addendum if platform_addendum else _PHASE_SYSTEM
    if escalate_to_council:
        return council.author_phase_plan(prompt=prompt, system=system, phase_num=phase_num)

    body = run_plan_author(
        prompt=prompt,
        system=system,
        required_phase_id=required_phase_id,
        project_dir=Path(target_dir),
    )
    return council.typed_plan_from_synthesizer_text(body, required_phase_id=required_phase_id)


def append_test_tasks(plan: str, test_tasks: list[str]) -> str:
    """Append documentation-example test tasks to a generated plan.

    Inserts the tasks before the final checklist item if one exists,
    or appends them at the end.
    """
    if not test_tasks:
        return plan
    lines = plan.rstrip().split("\n")
    # Find the last checklist item to insert before it.
    last_check_idx = None
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].lstrip().startswith("- ["):
            last_check_idx = i
            break
    if last_check_idx is not None:
        before = lines[:last_check_idx]
        after = lines[last_check_idx:]
        return "\n".join(before + test_tasks + after) + "\n"
    return "\n".join(lines) + "\n" + "\n".join(test_tasks) + "\n"


_BUGS_HEADING_RE = re.compile(r"^## Bugs\s*$", re.MULTILINE)

_MCLOOP_TAG_RE = re.compile(r"\[(USER|BATCH|AUTO)\]")
_TASK_LINE_PREFIX_RE = re.compile(r"^- \[[ xX]\] ")
_LEADING_DIRECTIVE_RE = re.compile(r"^(\s*\[(?:USER|BATCH|AUTO)\])+\s*")


def _escape_mcloop_tags(content: str) -> str:
    """Rewrite prose ``[USER]/[BATCH]/[AUTO]`` tokens as parenthesised form.

    mcloop parses these bracketed tokens as task-level directives when they
    appear at the start of a task body (immediately after ``- [ ] ``). When
    the same tokens appear mid-sentence inside a task description they get
    misinterpreted, silently changing how the task runs. To prevent that,
    scan every task line and rewrite any non-leading occurrence to
    ``(USER)`` / ``(BATCH)`` / ``(AUTO)``. Intentional leading directives
    (and any stacked leading directives) are preserved unchanged; non-task
    lines are left alone.
    """
    out_lines: list[str] = []
    for line in content.splitlines(keepends=True):
        stripped = line.lstrip()
        prefix_m = _TASK_LINE_PREFIX_RE.match(stripped)
        if not prefix_m:
            out_lines.append(line)
            continue
        indent = line[: len(line) - len(stripped)]
        checkbox = stripped[: prefix_m.end()]
        body = stripped[prefix_m.end() :]
        directive_m = _LEADING_DIRECTIVE_RE.match(body)
        directive = directive_m.group(0) if directive_m else ""
        remainder = body[len(directive) :]
        escaped = _MCLOOP_TAG_RE.sub(lambda m: f"({m.group(1)})", remainder)
        out_lines.append(indent + checkbox + directive + escaped)
    return "".join(out_lines)


def _strip_bugs_section(content: str) -> str:
    """Remove any ``## Bugs`` heading from *content*.

    ``## Bugs`` is an mcloop convention; duplo-generated PLAN.md must
    never emit it. If the LLM produced one, drop the heading and keep
    any task lines that were under it so feature work is not lost.
    """
    m = _BUGS_HEADING_RE.search(content)
    if m:
        before = content[: m.start()]
        after = content[m.end() :]
        content = before.rstrip("\n") + "\n" + after.strip("\n")
    return content.rstrip("\n") + "\n"


def save_plan(
    content: Plan | str,
    *,
    target_dir: Path | str = ".",
    expected_h1_ordinals: list[int] | None = None,
    extra_tasks: list[Task] | tuple[Task, ...] = (),
) -> Path:
    """Persist a phase plan to ``PLAN.md`` in *target_dir*.

    Per T-000186, plan content flows through the typed
    :class:`bob_tools.planfile.Plan` API end-to-end; the bytes on disk
    are produced by :func:`bob_tools.planfile.save` (which renders the
    Plan, runs ``assert_mcloop_canonical``, and writes atomically under
    a sidecar lock). ``save_plan`` itself no longer calls
    :meth:`pathlib.Path.write_text` on plan content.

    ``content`` accepts either a :class:`Plan` (the canonical typed
    path) or a markdown string (back-compat for callers that still
    pre-render). When a string is supplied, it is parsed via
    :func:`bob_tools.planfile.parse_plan` first, then routed through
    the same typed-persistence path.

    ``extra_tasks`` is the optional list of verification/contract
    tasks produced by :func:`format_verification_tasks` and
    :func:`format_contracts_as_verification`. Per T-000190 the helpers
    now hand back typed :class:`~bob_tools.planfile.Task` values, so
    this entry point appends them directly to the just-authored
    phase via :func:`bob_tools.planfile.add_phase_task`. No markdown
    round-trip is involved.

    Backward-compat behaviors retained from the legacy markdown path:

    * The deprecated ``## Bugs`` heading is stripped from any string
      content before parsing; tasks that were under it survive.
    * Mid-sentence ``[USER]/[BATCH]/[AUTO]`` tokens inside task bodies
      are rewritten to their parenthesized forms so mcloop does not
      mis-interpret them.
    * ``expected_h1_ordinals``, when provided, is checked against the
      accumulated PLAN.md text after persistence as a fail-closed
      backstop for the roadmap-state source-of-truth invariant. A
      violation does not roll back the write — that is now the
      planfile-layer concern.

    Returns the resolved PLAN.md path.
    """
    path = (Path(target_dir) / _PLAN_FILENAME).resolve()

    if isinstance(content, Plan):
        plan = content
    else:
        cleaned = _strip_bugs_section(content)
        cleaned = _escape_mcloop_tags(cleaned)
        try:
            plan = parse_plan(cleaned)
        except PlanSyntaxError as exc:
            # Legacy callers (notably the project-header preamble write
            # at pipeline.py ~1825) pass a pure-prose block with no
            # phase headers; parse_plan handles that as a preamble-only
            # plan, so a real syntax error here is exceptional.
            raise PlanValidationError(
                [f"save_plan: could not parse markdown content: {exc}"]
            ) from exc

    if extra_tasks:
        plan = _append_extra_tasks(plan, tuple(extra_tasks))

    if path.exists():
        existing_plan = planfile_load(path)
        plan = _merge_existing_plan(existing_plan, plan)

    planfile_save(path, plan, validation="unchecked")
    saved_text = path.read_text(encoding="utf-8")
    stamped_text = stamp_sequential_phase_ids(saved_text)
    if stamped_text != saved_text:
        path.write_text(stamped_text, encoding="utf-8")

    if expected_h1_ordinals is not None:
        accumulated = path.read_text(encoding="utf-8")
        validate_h1_ordinal_sequence(accumulated, expected_ordinals=expected_h1_ordinals)

    return path


def _merge_existing_plan(existing: Plan, new: Plan) -> Plan:
    """Return ``existing`` with phases / preamble / project_title from
    ``new`` folded in.

    Used by :func:`save_plan` when PLAN.md already exists on disk so
    each phase-generation call appends rather than overwrites. The
    merge rules:

    * If ``existing`` has no ``project_title`` and ``new`` does, the
      new one wins (the project-header preamble write happens before
      phases land, so the second invocation carries the title).
    * ``new``'s preamble is concatenated after ``existing``'s when
      both are non-empty; an empty side is dropped.
    * Phases are appended in order; ordinals on appended phases are
      renumbered so the merged sequence is contiguous and monotonic
      ``1..len(merged_phases)``. ``typed_plan_from_synthesizer_text``
      always emits ``ordinal=index+1`` within its own body
      (the synthesizer authors one phase at a time, so each new Plan
      arrives with ``ordinal=1``); without renumbering the merged
      plan would carry duplicate ordinals that fail mcloop's parser
      structural-sanity check on the next load. Phase identity stays
      with ``phase_id``; only the display-ordinal is renumbered.
    * Duplicate ``phase_id`` values are NOT silently de-duplicated —
      that remains the validator's job at save time (the canonical
      gate). Renumbering is a strictly cosmetic ordinal repair.
    """
    from bob_tools.planfile import migrate as planfile_migrate

    project_title = new.project_title or existing.project_title
    preamble_parts = [p for p in (existing.preamble, new.preamble) if p]
    preamble = "\n\n".join(preamble_parts)

    # Strip task ids from the inbound plan's phases so the post-merge
    # ``migrate`` reassignment (below) sees the existing tasks' ids as
    # the source of truth and continues numbering from
    # ``max(existing) + 1``. Without this, both ``existing`` and ``new``
    # carry their own ``T-000001..`` sequences (each ``migrate`` call
    # restarts at ``T-000001`` for a fresh plan), producing duplicate
    # ids when the phases are concatenated.
    new_phases_no_ids = tuple(_phase_with_clean_task_ids(phase) for phase in new.phases)

    combined = tuple(existing.phases) + new_phases_no_ids
    renumbered = tuple(
        dataclasses.replace(phase, ordinal=index + 1) for index, phase in enumerate(combined)
    )
    merged = dataclasses.replace(
        existing,
        project_title=project_title,
        preamble=preamble,
        phases=renumbered,
        bugs=existing.bugs or new.bugs,
    )
    return planfile_migrate(merged)


def _phase_with_clean_task_ids(phase: Phase) -> Phase:
    """Return ``phase`` with task ids cleared on every (sub)task tree.

    Used by :func:`_merge_existing_plan` so the post-merge :func:`migrate`
    can reassign monotonic ids from ``max(existing) + 1`` without
    colliding with the inbound plan's own ``T-000001..`` sequence.
    """
    return dataclasses.replace(
        phase,
        tasks=tuple(_task_without_id(t) for t in phase.tasks),
        subsections=tuple(
            dataclasses.replace(
                sub,
                tasks=tuple(_task_without_id(t) for t in sub.tasks),
            )
            for sub in phase.subsections
        ),
    )


def _task_without_id(task):
    """Return ``task`` (and its children, recursively) with ``task_id``
    set to ``None`` so :func:`migrate` reassigns a fresh id at merge time.
    """
    return dataclasses.replace(
        task,
        task_id=None,
        children=tuple(_task_without_id(child) for child in task.children),
    )


def _append_extra_tasks(plan: Plan, extra_tasks: tuple[Task, ...]) -> Plan:
    """Append each typed task in ``extra_tasks`` to ``plan``'s final phase.

    The verification helpers (``format_verification_tasks`` and
    ``format_contracts_as_verification``) hand back fresh tasks built
    via :func:`make_task`. :func:`add_phase_task` validates the
    resulting plan in constructed mode, which requires
    ``magic_version=1`` and every task to carry a stable ``T-NNNNNN``
    id. Plans handed in by the string-content path of
    :func:`save_plan` come straight from :func:`parse_plan` and may
    carry neither, so this helper runs them through :func:`migrate`
    before appending.
    """
    if not plan.phases or not extra_tasks:
        # No phase to attach verification tasks to (project-header
        # preamble write). Append nothing; the verification block is
        # only emitted on phase content writes.
        return plan

    if plan.magic_version is None or any(
        task.task_id is None for phase in plan.phases for task in phase.tasks
    ):
        plan = planfile_migrate(
            dataclasses.replace(plan, magic_version=1) if plan.magic_version is None else plan
        )

    last_phase = plan.phases[-1]
    target_phase_id = last_phase.phase_id
    if target_phase_id is None:
        return plan

    merged = plan
    for task in extra_tasks:
        merged, _assigned = add_phase_task(merged, target_phase_id, _rebuild_task(task))
    return merged


def _rebuild_task(task: Task) -> Task:
    """Return ``task`` rebuilt with no source-line metadata so
    :func:`add_phase_task`'s constructed-mode harness accepts it.

    Tasks reach this helper either fresh from :func:`make_task`
    (already stripped) or — historically — out of :func:`parse_plan`,
    which attaches ``line_number`` and possibly ``trailing_lines``.
    Rebuilding via :func:`make_task` clears both while preserving the
    task's structural fields and any nested children (recursively).
    """
    rebuilt_children = tuple(_rebuild_task(child) for child in task.children)
    return make_task(
        task.text,
        status=task.status,
        flag_tags=task.flag_tags,
        action_tag=task.action_tag,
        annotations=task.annotations,
        deps=task.deps,
        children=rebuilt_children,
        ruled_out=tuple(task.ruled_out),
        task_id=task.task_id,
    )
