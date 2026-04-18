"""Entry point for ``duplo init``.

Implementation shape for the ``duplo init`` subcommand per
INIT-design.md § "Implementation shape": a single module exposing
one :func:`run_init` entry point. ``duplo/main.py`` dispatches here
when ``sys.argv[1] == "init"``.

Currently covers the no-arguments flow and the URL-only flow. The
``--from-description`` and combined cases are fleshed out in
subsequent subphases.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from duplo.claude_cli import ClaudeCliError
from duplo.diagnostics import record_failure
from duplo.fetcher import fetch_site
from duplo.scanner import scan_directory  # noqa: F401
from duplo.spec_reader import ProductSpec, ReferenceEntry, SourceEntry
from duplo.spec_writer import (
    DraftInputs,
    _build_draft_spec,
    _propose_file_role,
    draft_spec,
    format_spec,
)
from duplo.url_canon import canonicalize_url
from duplo.validator import validate_product_url

# Static body of ``ref/README.md`` per INIT-design.md § "ref/README.md
# content".  Written once by :func:`run_init` and never modified by
# duplo afterward.
_REF_README_CONTENT = """# ref/

Drop reference files here that you want duplo to use as
authoritative examples of what you're building.

Accepted file types:
  - Images: png, jpg, gif, webp (UI screenshots, mockups, logos)
  - Videos: mp4, mov, webm, avi (demos, walkthroughs)
  - PDFs: spec documents, design guides, API docs
  - Text/markdown: notes, constraints, requirements

**This directory can be empty.** If SPEC.md's ## Sources section
gives duplo a URL that covers what you want, you don't need any
files here. Add files only when you want to supplement or override
what duplo can learn from the URL.

Each file you add should be listed in SPEC.md's ## References
section with a role (visual-target, behavioral-target, docs,
counter-example, ignore). When you add files and re-run duplo,
duplo will propose role entries for you to confirm or edit.

See SPEC-guide.md (in the project root) for details on each
role and when to use which.
"""

_SPEC_EXISTS_ERROR = (
    "Error: SPEC.md already exists in this directory.\n"
    "  Use `duplo init --force` to overwrite (your existing SPEC.md\n"
    "  will be lost).\n"
    "  Use `duplo` to run against your existing SPEC.md."
)

_NO_ARGS_NEXT_STEPS = """Next steps:
  1. Open SPEC.md in your editor. Replace each <FILL IN> marker
     with your content. See SPEC-guide.md for details on each
     section.
  2. (Optional) Drop reference files into ref/ — screenshots,
     videos, PDFs, design mockups. Skip this if you'll provide
     a URL or rely on prose alone.
  3. (Optional) Add a URL to ## Sources in SPEC.md if you have
     a product to draw from.
  4. Run `duplo` to extract features and generate the build plan."""

_URL_NEXT_STEPS_IDENTIFIED = """Next steps:
  1. Open SPEC.md in your editor. Review the pre-filled sections
     and replace any remaining <FILL IN> markers (## Architecture
     is required).
  2. (Optional) Drop reference files into ref/ if you want
     specific visual direction the URL doesn't show, or behavior
     the URL doesn't capture.
  3. Run `duplo` to do the full crawl, extract features, and
     generate the build plan."""

_URL_NEXT_STEPS_UNIDENTIFIED = """Next steps:
  1. Open SPEC.md and fill in ## Purpose, ## Architecture
     manually.
  2. (Optional) Drop reference files into ref/ if you want
     specific visual direction the URL doesn't show.
  3. Run `duplo` to do the full crawl, extract features, and
     generate the build plan."""

_URL_NEXT_STEPS_FETCH_FAILED = """Next steps:
  1. Open SPEC.md and fill in ## Purpose, ## Architecture, and
     any other required sections.
  2. The URL has been added to ## Sources with `scrape: none`.
     Change it to `scrape: deep` (or `shallow`) once the network
     issue is resolved.
  3. Run `duplo` to extract features and generate the build plan."""

_DESCRIPTION_NEXT_STEPS = """Next steps:
  1. Open SPEC.md in your editor. Verify the drafted sections
     match your intent. Edit anything duplo got wrong.
  2. (Optional) Add a URL to ## Sources or drop files into ref/
     if you have additional reference material.
  3. Run `duplo` to extract features and generate the build plan."""

_DESCRIPTION_FILE_NOT_FOUND = "Error: file not found: {path}"

_STDIN_TTY_PROMPT = "Reading description from stdin. Press Ctrl-D when done."

_INVALID_URL_ERROR = (
    "Error: {url!r} is not a valid URL.\n"
    "  URLs must start with http:// or https://.\n"
    "  To set up without a URL, run `duplo init` (no arguments)."
)

_COMBINED_NEXT_STEPS = """Next steps:
  1. Open SPEC.md in your editor. Verify the drafted sections
     match your intent. Edit anything duplo got wrong.
  2. (Optional) Drop reference files into ref/ if you want
     additional visual direction or behavior examples.
  3. Run `duplo` to do the full crawl, extract features, and
     generate the build plan."""

_URL_DEFERRED_DEEP_NOTE = (
    "Note: duplo will deep-crawl {url} on the next run.\n"
    "The deep scrape is deferred so you can adjust ## Sources first\n"
    "(e.g. set scrape: none, or add other URLs to crawl)."
)

_URL_FETCH_FAILED_PRELUDE = (
    "duplo can still set up the project without scraping the URL.\n"
    "Continuing with template-only setup."
)


def run_init(args: argparse.Namespace) -> None:
    """Run the ``duplo init`` flow.

    The single entry point for the subcommand. Dispatches to the
    appropriate input-combination handler based on ``args.url`` and
    ``args.from_description``. Delegates to existing utilities; see
    INIT-design.md § "Implementation shape" for the dependency list.

    Args:
        args: Parsed argparse namespace with fields ``url``,
            ``from_description``, ``deep``, and ``force``.
    """
    url = getattr(args, "url", None)
    from_description = getattr(args, "from_description", None)
    if url is None and from_description is None:
        _run_no_args(args)
        return
    if url is not None and from_description is None:
        _run_url(args, url)
        return
    if url is None and from_description is not None:
        _run_description(args, from_description)
        return
    _run_combined(args, url, from_description)


def _run_no_args(args: argparse.Namespace) -> None:
    """Handle ``duplo init`` with no URL and no ``--from-description``.

    Per INIT-design.md § "duplo init (no arguments)": writes a
    template-only SPEC.md (via ``format_spec`` on an empty
    ``ProductSpec``), creates the ``ref/`` directory if absent, and
    writes ``ref/README.md`` if absent.  Honors ``--force`` to
    overwrite an existing SPEC.md; otherwise errors and exits 1.
    """
    cwd = Path.cwd()
    spec_path = cwd / "SPEC.md"
    force = bool(getattr(args, "force", False))

    if spec_path.exists() and not force:
        print(_SPEC_EXISTS_ERROR, file=sys.stderr)
        sys.exit(1)

    ref_dir = cwd / "ref"
    ref_created = not ref_dir.exists()
    ref_dir.mkdir(exist_ok=True)
    if ref_created:
        print("Created ref/ (empty).")

    readme_path = ref_dir / "README.md"
    if not readme_path.exists():
        readme_path.write_text(_REF_README_CONTENT)
        print("Created ref/README.md.")

    spec_path.write_text(format_spec(ProductSpec()))
    print("Wrote SPEC.md (template, no inputs).")
    print()
    print(_NO_ARGS_NEXT_STEPS)


def _identify_product(canonical_url: str, text: str) -> tuple[str, bool]:
    """Run the validator on scraped *text* and return ``(product_name, identified)``.

    *identified* is True when the validator reports a single clearly
    identifiable product with a non-empty name.  An LLM failure is
    swallowed and treated as unidentified (the URL-only init flow
    keeps going on the pre-filled Sources entry alone).
    """
    try:
        result = validate_product_url(canonical_url, text=text)
    except ClaudeCliError as exc:
        record_failure(
            "init:_identify_product",
            "llm",
            f"validate_product_url failed for {canonical_url}: {exc}",
            context={"url": canonical_url},
        )
        return ("", False)
    if result.single_product and not result.unclear_boundaries and result.product_name:
        return (result.product_name, True)
    return ("", False)


def _scan_existing_ref_files(cwd: Path) -> tuple[list[Path], dict[Path, str]]:
    """Inventory user files in ``ref/`` and propose a role for each.

    Per INIT-design.md § "ref/ already exists with files": when the
    user has dropped reference material into ``ref/`` before running
    ``duplo init``, we call :func:`_propose_file_role` on each file so
    the resulting SPEC.md can pre-fill ``## References`` with
    ``proposed: true`` entries.

    Returns ``(paths, proposals)`` where *paths* are ``ref/<name>``
    relative to the project root (matching :class:`ReferenceEntry`
    path convention) and *proposals* maps each path to the role
    :func:`_propose_file_role` returned.  Skips hidden files and
    ``README.md`` (duplo-owned).  Returns empty lists when ``ref/``
    does not exist or contains no eligible files.
    """
    ref_dir = cwd / "ref"
    if not ref_dir.is_dir():
        return ([], {})
    paths: list[Path] = []
    proposals: dict[Path, str] = {}
    for entry in sorted(ref_dir.iterdir()):
        if not entry.is_file():
            continue
        if entry.name.startswith("."):
            continue
        if entry.name == "README.md":
            continue
        _, role = _propose_file_role(entry)
        rel = Path("ref") / entry.name
        paths.append(rel)
        proposals[rel] = role
    return (paths, proposals)


def _append_proposed_references(
    spec: ProductSpec,
    paths: list[Path],
    proposals: dict[Path, str],
) -> None:
    """Append a ``proposed: true`` ReferenceEntry per scanned ref/ file.

    Mirrors the loop in :func:`duplo.spec_writer._build_draft_spec` so
    URL-flow branches that bypass :func:`draft_spec` (unidentified and
    fetch-failure) still surface user files from ``ref/`` as proposed
    References entries in SPEC.md.
    """
    for path in paths:
        role = proposals.get(path, "")
        roles = [role] if role else []
        spec.references.append(ReferenceEntry(path=path, roles=roles, proposed=True))


def _run_url(args: argparse.Namespace, url: str) -> None:
    """Handle ``duplo init <url>`` per INIT-design.md § "duplo init <url>".

    Canonicalizes the URL (so SPEC.md entries match what later
    fetches produce regardless of trailing-slash / case noise),
    fetches it at the configured depth (shallow by default, deep
    when ``--deep`` is set), asks the validator to identify the
    product, and writes a drafted SPEC.md via :func:`draft_spec`.

    On fetch failure the URL is still added to ``## Sources`` with
    ``scrape: none`` so the user can re-enable scraping once the
    network issue is resolved; the rest of the spec is the
    template.
    """
    cwd = Path.cwd()
    spec_path = cwd / "SPEC.md"
    force = bool(getattr(args, "force", False))
    deep = bool(getattr(args, "deep", False))

    if spec_path.exists() and not force:
        print(_SPEC_EXISTS_ERROR, file=sys.stderr)
        sys.exit(1)

    canonical = canonicalize_url(url)
    scrape_depth = "deep" if deep else "shallow"
    depth_label = "deep scrape" if deep else "shallow scrape"

    try:
        text, _examples, _structures, records, _raw = fetch_site(
            canonical, scrape_depth=scrape_depth
        )
    except Exception as exc:  # noqa: BLE001 — any fetch failure falls back to template
        record_failure(
            "init:_run_url",
            "fetch",
            f"fetch_site raised for {canonical}: {exc}",
            context={"url": canonical},
        )
        text, records = "", []
    fetch_ok = bool(records)

    product_name = ""
    identified = False
    if fetch_ok and text:
        product_name, identified = _identify_product(canonical, text)

    existing_refs, vision_proposals = _scan_existing_ref_files(cwd)

    if fetch_ok:
        if identified:
            print(f"Fetched {canonical} ({depth_label} for product identity).")
            print(f"  → Identified product: {product_name}")
            print("  → Pre-filled ## Purpose, ## Sources")
            print()
            inputs = DraftInputs(
                url=canonical,
                url_scrape=text,
                existing_ref_files=existing_refs,
                vision_proposals=vision_proposals,
            )
            content = draft_spec(inputs)
        else:
            print(f"Fetched {canonical}.")
            print("  → Could not identify a specific product from the page content.")
            print("  → Pre-filled ## Sources only.")
            print()
            spec = ProductSpec()
            spec.sources.append(
                SourceEntry(url=canonical, role="product-reference", scrape="deep")
            )
            _append_proposed_references(spec, existing_refs, vision_proposals)
            content = format_spec(spec)
    else:
        print(f"Fetching {canonical} ...")
        print("  → Failed: could not fetch URL.")
        print()
        print(_URL_FETCH_FAILED_PRELUDE)
        print()
        spec = ProductSpec()
        spec.sources.append(SourceEntry(url=canonical, role="product-reference", scrape="none"))
        _append_proposed_references(spec, existing_refs, vision_proposals)
        content = format_spec(spec)

    ref_dir = cwd / "ref"
    ref_created = not ref_dir.exists()
    ref_dir.mkdir(exist_ok=True)
    if ref_created:
        print("Created ref/ (empty).")

    readme_path = ref_dir / "README.md"
    if not readme_path.exists():
        readme_path.write_text(_REF_README_CONTENT)
        print("Created ref/README.md.")

    spec_path.write_text(content)
    if fetch_ok:
        print("Wrote SPEC.md.")
    else:
        print("Wrote SPEC.md (template).")
    print()

    if not fetch_ok:
        print(_URL_NEXT_STEPS_FETCH_FAILED)
    elif identified:
        print(_URL_NEXT_STEPS_IDENTIFIED)
        if not deep:
            print()
            print(_URL_DEFERRED_DEEP_NOTE.format(url=canonical))
    else:
        print(_URL_NEXT_STEPS_UNIDENTIFIED)
        if not deep:
            print()
            print(_URL_DEFERRED_DEEP_NOTE.format(url=canonical))


def _read_description(path_arg: str) -> tuple[str, str] | None:
    """Read the description text from *path_arg*.

    *path_arg* is either ``"-"`` (read from stdin) or a filesystem
    path.  Returns ``(text, source_label)`` on success where
    *source_label* is ``"stdin"`` or the path as given.  Returns
    ``None`` when the path does not exist (caller reports and
    exits).  The TTY prompt for stdin is emitted here so the
    "Reading..." line appears before the user starts typing.
    """
    if path_arg == "-":
        if sys.stdin.isatty():
            print(_STDIN_TTY_PROMPT)
        text = sys.stdin.read()
        return (text, "stdin")
    path = Path(path_arg)
    if not path.is_file():
        print(_DESCRIPTION_FILE_NOT_FOUND.format(path=path_arg), file=sys.stderr)
        return None
    text = path.read_text()
    return (text, path_arg)


def _describe_drafted_sections(spec: ProductSpec) -> list[str]:
    """Return per-section bullets for the description-flow output.

    Per INIT-design.md § "duplo init --from-description
    description.txt": after ``Wrote SPEC.md.``, print bullets that
    tell the user which sections the drafter pre-filled vs. left as
    ``<FILL IN>`` / empty.  The exact set of bullets depends on the
    content of the drafted :class:`ProductSpec` — sections the LLM
    filled report as pre-filled; required sections the LLM left
    null report as ``<FILL IN>``; optional sections left empty
    report as empty.  ``## Notes`` always reports as containing the
    verbatim description (populated by :func:`_build_draft_spec`
    step 2, never by the LLM).
    """
    prefilled: list[str] = []
    if spec.purpose:
        prefilled.append("## Purpose")
    if spec.design.user_prose:
        prefilled.append("## Design")

    bullets: list[str] = []
    if prefilled:
        bullets.append(f"  → Pre-filled {', '.join(prefilled)} from prose.")

    if spec.architecture:
        bullets.append("  → ## Architecture filled from prose (description specified a stack).")
    else:
        bullets.append(
            "  → ## Architecture left as <FILL IN> (description did not state a stack explicitly)."
        )

    if not spec.behavior_contracts:
        bullets.append("  → ## Behavior left empty (no input/output pairs detected).")
    else:
        bullets.append(
            f"  → ## Behavior filled with {len(spec.behavior_contracts)} "
            "input/output pair(s) from prose."
        )

    bullets.append("  → ## Notes contains the verbatim original description.")
    return bullets


def _run_description(args: argparse.Namespace, path_arg: str) -> None:
    """Handle ``duplo init --from-description PATH`` per INIT-design.md.

    Reads prose from *path_arg* (a file path or ``-`` for stdin) and
    drafts SPEC.md from it via :func:`_build_draft_spec`.  Prints
    per-section bullets describing what the drafter pre-filled vs.
    left as ``<FILL IN>``.  The original prose is preserved
    verbatim in ``## Notes``; URLs mentioned in the prose become
    ``proposed: true`` Sources entries (the drafter handles URL
    extraction).
    """
    cwd = Path.cwd()
    spec_path = cwd / "SPEC.md"
    force = bool(getattr(args, "force", False))

    if spec_path.exists() and not force:
        print(_SPEC_EXISTS_ERROR, file=sys.stderr)
        sys.exit(1)

    read_result = _read_description(path_arg)
    if read_result is None:
        sys.exit(1)
    description, source_label = read_result

    print(f"Read {len(description)} chars of description from {source_label}.")

    existing_refs, vision_proposals = _scan_existing_ref_files(cwd)
    inputs = DraftInputs(
        description=description,
        existing_ref_files=existing_refs,
        vision_proposals=vision_proposals,
    )
    spec = _build_draft_spec(inputs)
    content = format_spec(spec)
    print("Drafted SPEC.md from description.")
    print()

    ref_dir = cwd / "ref"
    ref_created = not ref_dir.exists()
    ref_dir.mkdir(exist_ok=True)
    if ref_created:
        print("Created ref/ (empty).")

    readme_path = ref_dir / "README.md"
    if not readme_path.exists():
        readme_path.write_text(_REF_README_CONTENT)
        print("Created ref/README.md.")

    spec_path.write_text(content)
    print("Wrote SPEC.md.")
    print()

    for bullet in _describe_drafted_sections(spec):
        print(bullet)
    print()
    print(_DESCRIPTION_NEXT_STEPS)


def _run_combined(args: argparse.Namespace, url: str, path_arg: str) -> None:
    """Handle ``duplo init <url> --from-description PATH`` per INIT-design.md.

    The drafter merges URL scrape and prose, with prose winning on
    conflicts (see DRAFTER-design.md § "Drafting from inputs" — the
    LLM is told that architecture comes ONLY from prose, and is asked
    to reconcile design/scope/behavior across both inputs with prose
    taking precedence).

    Errors stack: if the URL is malformed AND the description file is
    missing, both errors are reported to stderr and nothing is
    written (per INIT-design.md § "Both init arguments invalid").

    On URL fetch failure, the flow falls back to description-only
    drafting and records the URL in ``## Sources`` with
    ``scrape: none`` (matching the URL-only fetch-failure path) so
    the user can retry the crawl on the next ``duplo`` run.
    """
    cwd = Path.cwd()
    spec_path = cwd / "SPEC.md"
    force = bool(getattr(args, "force", False))
    deep = bool(getattr(args, "deep", False))

    if spec_path.exists() and not force:
        print(_SPEC_EXISTS_ERROR, file=sys.stderr)
        sys.exit(1)

    errors: list[str] = []
    if not url.startswith(("http://", "https://")):
        errors.append(_INVALID_URL_ERROR.format(url=url))

    # Description file existence is checked up front so the error
    # stacks with the URL-validation error.  Stdin is always
    # "available" syntactically; we defer the read itself until after
    # validation so the TTY prompt appears after any error output
    # would have been flushed (which it isn't, since errors cause
    # exit, but this keeps the order clean on the happy path).
    description_from_stdin = path_arg == "-"
    if not description_from_stdin:
        desc_path = Path(path_arg)
        if not desc_path.is_file():
            errors.append(_DESCRIPTION_FILE_NOT_FOUND.format(path=path_arg))

    if errors:
        for message in errors:
            print(message, file=sys.stderr)
        sys.exit(1)

    if description_from_stdin:
        if sys.stdin.isatty():
            print(_STDIN_TTY_PROMPT)
        description = sys.stdin.read()
        source_label = "stdin"
    else:
        description = Path(path_arg).read_text()
        source_label = path_arg

    canonical = canonicalize_url(url)
    scrape_depth = "deep" if deep else "shallow"
    depth_label = "deep scrape" if deep else "shallow scrape"

    try:
        text, _examples, _structures, records, _raw = fetch_site(
            canonical, scrape_depth=scrape_depth
        )
    except Exception as exc:  # noqa: BLE001 — any fetch failure falls back to description-only
        record_failure(
            "init:_run_combined",
            "fetch",
            f"fetch_site raised for {canonical}: {exc}",
            context={"url": canonical},
        )
        text, records = "", []
    fetch_ok = bool(records)

    product_name = ""
    identified = False
    if fetch_ok and text:
        product_name, identified = _identify_product(canonical, text)

    existing_refs, vision_proposals = _scan_existing_ref_files(cwd)

    if fetch_ok:
        if identified:
            print(f"Fetched {canonical} ({depth_label} for product identity).")
            print(f"  → Identified product: {product_name}")
        else:
            print(f"Fetched {canonical}.")
            print("  → Could not identify a specific product from the page content.")
    else:
        print(f"Fetching {canonical} ...")
        print("  → Failed: could not fetch URL.")
        print()
        print(_URL_FETCH_FAILED_PRELUDE)

    print(f"Read {len(description)} chars of description from {source_label}.")
    print("Drafted SPEC.md from URL and description (prose wins on conflicts).")
    print()

    inputs = DraftInputs(
        url=canonical if fetch_ok else None,
        url_scrape=text if fetch_ok else None,
        description=description,
        existing_ref_files=existing_refs,
        vision_proposals=vision_proposals,
    )
    spec = _build_draft_spec(inputs)
    if not fetch_ok:
        # URL fetch failed, but we still record it so the user can
        # re-enable scraping after fixing the network issue.  Matches
        # the URL-only fetch-failure path.
        spec.sources.insert(
            0,
            SourceEntry(url=canonical, role="product-reference", scrape="none"),
        )
    content = format_spec(spec)

    ref_dir = cwd / "ref"
    ref_created = not ref_dir.exists()
    ref_dir.mkdir(exist_ok=True)
    if ref_created:
        print("Created ref/ (empty).")

    readme_path = ref_dir / "README.md"
    if not readme_path.exists():
        readme_path.write_text(_REF_README_CONTENT)
        print("Created ref/README.md.")

    spec_path.write_text(content)
    print("Wrote SPEC.md.")
    print()

    for bullet in _describe_drafted_sections(spec):
        print(bullet)
    print()
    print(_COMBINED_NEXT_STEPS)
