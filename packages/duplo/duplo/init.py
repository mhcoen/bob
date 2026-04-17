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
from duplo.spec_reader import ProductSpec, SourceEntry
from duplo.spec_writer import DraftInputs, _propose_file_role, draft_spec, format_spec
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
    raise NotImplementedError("duplo init --from-description is not yet implemented")


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

    text, _examples, _structures, records, _raw = fetch_site(canonical, scrape_depth=scrape_depth)
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
            content = format_spec(spec)
    else:
        print(f"Fetching {canonical} ...")
        print("  → Failed: could not fetch URL.")
        print()
        print(_URL_FETCH_FAILED_PRELUDE)
        print()
        spec = ProductSpec()
        spec.sources.append(SourceEntry(url=canonical, role="product-reference", scrape="none"))
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
