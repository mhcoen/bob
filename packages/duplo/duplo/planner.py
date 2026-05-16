"""Generate PLAN.md files for building phases of an application."""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from duplo import council
from duplo.claude_cli import query
from duplo.extractor import Feature
from duplo.questioner import BuildPreferences

_PHASE_SYSTEM = """\
You are a senior software architect generating a build plan for one
phase of an application.

You will be given a roadmap phase (number, title, goal, features,
test criteria) along with build preferences. Generate a PLAN.md
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
  content with the H1 phase heading line, then go directly to
  task checkboxes.
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

Output ONLY the Markdown for PLAN.md. No explanation outside it.
Format:

# <AppName> — Phase N: <Title>

- [ ] Set up project structure and build system
- [ ] [BATCH] Add user authentication [feat: "User authentication"]
  - [ ] Create `AuthService.swift` with `login(email:password:)` and `signup(email:password:)` methods
  - [ ] Add `LoginView.swift` with email/password fields and submit button
  - [ ] Wire `AuthService` into the app lifecycle, store session token in Keychain
- [ ] Fix input validation on signup [fix: "email format not checked"]
- [ ] ...

The heading MUST use the exact format shown: app name, em dash (—),
Phase N, colon, then the phase title. Use the project name as the
app name. The phase number and title are provided in the prompt.
"""

_NEXT_PHASE_SYSTEM = """\
You are a senior software architect helping to plan the next phase of an application build.

Given the completed phase plan, user feedback, and (optionally) visual issues from
screenshot comparison, produce the next phase PLAN.md in Markdown. The next phase must:
- Build incrementally on what was completed in the previous phase
- Address all user feedback items
- Fix any visual issues identified in screenshot comparison
- Add the next most valuable batch of features (not everything at once)

Output ONLY the Markdown for PLAN.md. Do not add any explanation outside the Markdown.
Use the following structure exactly:

# Phase N: <short title>

## Objective
One or two sentences describing what this phase accomplishes.

## Addresses
A bullet list of user feedback items and visual issues being resolved in this phase.
Omit this section if there is no feedback or visual issues.

## Features in scope
A bullet list of new features or improvements being added.

## Implementation steps
A numbered list of concrete implementation steps. Each step must be specific enough
for a developer to act on without ambiguity.
- Every step that implements one or more features MUST end with a
  [feat: "Feature Name"] annotation. If a step addresses multiple
  features, list them comma-separated:
  [feat: "Push-to-talk recording", "Global keyboard shortcuts"].
  Steps that fix bugs or visual issues use [fix: "description"].
  Scaffolding or structural steps that do not map to any feature
  use no annotation.
- When a step has multiple subtasks that are all specific enough
  to be executed without design decisions, mark the parent step
  with [BATCH] so McLoop combines the subtasks into a single
  session for efficiency.
- Reserve [USER] only for genuinely human-only checks with no
  scriptable form. Runnable verification must be expressed as a
  helper-script creation step plus an [AUTO:run_cli] step that
  invokes the helper with an absolute command path.

## Success criteria
A checklist of observable outcomes that confirm this phase is complete and working.

## Out of scope
A brief bullet list of items deliberately deferred to later phases.
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
_PHASE_H1_STRIP_RE = re.compile(
    r"^#+\s+.*?\b(?:stage|phase)\s+(\d+)\b", re.IGNORECASE
)

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

    expected_contig = list(
        range(ordinals[0], ordinals[0] + len(ordinals))
    )
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
) -> str:
    """Generate a PLAN.md for a specific roadmap phase.

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

    Returns:
        Markdown string suitable for writing to PLAN.md. The caller
        injects any visual-design section after the phase heading; this
        function never emits design prose as a preamble before the
        heading, which would confuse mcloop's phase parser.
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
{issues_block}{spec_block}{prior_files_block}
Generate the PLAN.md now.
"""

    system = _PHASE_SYSTEM + platform_addendum if platform_addendum else _PHASE_SYSTEM
    if council.is_enabled():
        raw = _strip_fences(
            council.author_phase_plan(
                prompt=prompt, system=system, phase_num=phase_num
            )
        )
    else:
        raw = _strip_fences(query(prompt, system=system))
    with_heading = _ensure_h1_heading(raw, project_name, phase_num, phase_title)
    return _strip_trailing_commentary(with_heading)


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
    content: str,
    *,
    target_dir: Path | str = ".",
    expected_h1_ordinals: list[int] | None = None,
) -> Path:
    """Write *content* to ``PLAN.md`` in *target_dir*.

    If PLAN.md already exists, new content is appended after a blank
    line so that existing checked and unchecked items are preserved.

    The written content never contains a ``## Bugs`` section: if *content*
    (e.g. from an LLM) includes one, the heading is stripped and any
    tasks that were under it are kept above. ``## Bugs`` is an mcloop
    convention that duplo does not emit.

    ``expected_h1_ordinals`` (when provided) is the source-of-truth list
    of phase ordinals Duplo's roadmap has emitted across all save_plan
    calls in this run, in document order. Forwarded to
    ``validate_h1_ordinal_sequence`` for an exact-match check against
    the accumulated PLAN.md. When None, the validator falls back to
    the internal contiguity check (backward-compatible path for
    callers that don't track roadmap state explicitly).

    Returns the path.
    """
    content = _strip_bugs_section(content)
    content = _escape_mcloop_tags(content)
    path = (Path(target_dir) / _PLAN_FILENAME).resolve()
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        accumulated = existing.rstrip("\n") + "\n\n" + content
    else:
        accumulated = content

    # H1 ordinal sequence check on the FULL accumulated PLAN.md
    # (after Duplo's strip-and-render in _ensure_h1_heading has run
    # on the per-phase content). When ``expected_h1_ordinals`` is
    # provided, the check is exact-match against Duplo's roadmap
    # state. When None, falls back to internal contiguity.
    # Either path raises BEFORE writing so a violation leaves
    # PLAN.md untouched. The check is a no-op when no canonical
    # H1 phase headings are present (e.g., during a pre-canonical
    # scaffold write where the body has not yet been wrapped in the
    # ``# <project> — Phase N: <title>`` envelope).
    validate_h1_ordinal_sequence(
        accumulated, expected_ordinals=expected_h1_ordinals
    )

    path.write_text(accumulated, encoding="utf-8")
    return path
