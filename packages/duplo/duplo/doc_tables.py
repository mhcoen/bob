"""Extract feature tables, operation lists, unit lists, and function refs from HTML."""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag

# Heading text patterns that indicate a table/list describes features.
_FEATURE_HEADING = re.compile(
    r"\b(features?|capabilit|functionalit|supported|available)\b",
    re.IGNORECASE,
)

# Heading text patterns for operations / methods / endpoints.
_OPERATION_HEADING = re.compile(
    r"\b(operations?|methods?|endpoints?|commands?|actions?|routes?|requests?)\b",
    re.IGNORECASE,
)

# Heading text patterns for units / types / enums / constants.
_UNIT_HEADING = re.compile(
    r"\b(units?|types?|enums?|constants?|values?|formats?|modes?|options?|flags?)\b",
    re.IGNORECASE,
)

# Heading text patterns for function / method references.
_FUNCTION_HEADING = re.compile(
    r"\b(functions?|methods?|api|class(?:es)?|interface|signature|constructor)\b",
    re.IGNORECASE,
)

# Pattern that matches a function/method signature in text.
_SIGNATURE_RE = re.compile(
    r"(?:^|\s)"
    r"(?:(?:def|func|function|fn|sub|proc|method)\s+)?"
    r"(\w+)\s*\([^)]*\)",
    re.MULTILINE,
)


@dataclass
class FeatureTable:
    """A table of features extracted from documentation."""

    heading: str
    rows: list[dict[str, str]]
    source_url: str


@dataclass
class OperationList:
    """A list of operations/methods/endpoints."""

    heading: str
    items: list[str]
    source_url: str


@dataclass
class UnitList:
    """A list of units, types, enums, or constants."""

    heading: str
    items: list[str]
    source_url: str


@dataclass
class FunctionRef:
    """A function or method reference with signature and description."""

    name: str
    signature: str
    description: str
    source_url: str


@dataclass
class DocStructures:
    """All structured data extracted from a documentation page."""

    feature_tables: list[FeatureTable] = field(default_factory=list)
    operation_lists: list[OperationList] = field(default_factory=list)
    unit_lists: list[UnitList] = field(default_factory=list)
    function_refs: list[FunctionRef] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(
            self.feature_tables or self.operation_lists or self.unit_lists or self.function_refs
        )

    def merge(self, other: DocStructures) -> None:
        self.feature_tables.extend(other.feature_tables)
        self.operation_lists.extend(other.operation_lists)
        self.unit_lists.extend(other.unit_lists)
        self.function_refs.extend(other.function_refs)


def extract_doc_structures(html: str, source_url: str = "") -> DocStructures:
    """Extract feature tables, operation lists, unit lists, and function refs.

    Scans the HTML for ``<table>`` elements, ``<ul>``/``<ol>`` lists, and
    ``<dl>`` definition lists. Uses the nearest heading to classify each
    structure as a feature table, operation list, unit list, or function
    reference.
    """
    soup = BeautifulSoup(html, "lxml")
    result = DocStructures()

    _extract_tables(soup, source_url, result)
    _extract_lists(soup, source_url, result)
    _extract_dl(soup, source_url, result)
    _extract_code_signatures(soup, source_url, result)

    return result


def _nearest_heading(tag: Tag) -> str:
    """Find the nearest preceding heading text for *tag*."""
    for sibling in tag.previous_siblings:
        if isinstance(sibling, Tag) and re.match(r"^h[1-6]$", sibling.name):
            return sibling.get_text(separator=" ").strip()
    parent = tag.parent
    if parent and isinstance(parent, Tag):
        for sibling in parent.previous_siblings:
            if isinstance(sibling, Tag) and re.match(r"^h[1-6]$", sibling.name):
                return sibling.get_text(separator=" ").strip()
        if parent.name == "div" or parent.name == "section":
            heading = parent.find(re.compile(r"^h[1-6]$"))
            if heading and isinstance(heading, Tag):
                return heading.get_text(separator=" ").strip()
    return ""


def _extract_tables(soup: BeautifulSoup, source_url: str, result: DocStructures) -> None:
    """Extract structured tables classified by their heading."""
    for table in soup.find_all("table"):
        if not isinstance(table, Tag):
            continue
        headers = []
        for th in table.find_all("th"):
            headers.append(th.get_text(separator=" ").strip())
        if not headers:
            continue

        rows: list[dict[str, str]] = []
        for tr in table.find_all("tr"):
            cells = tr.find_all("td")
            if not cells:
                continue
            row = {}
            for idx, td in enumerate(cells):
                key = headers[idx] if idx < len(headers) else f"col_{idx}"
                row[key] = td.get_text(separator=" ").strip()
            if any(row.values()):
                rows.append(row)

        if not rows:
            continue

        heading = _nearest_heading(table)
        header_text = f"{heading} {' '.join(headers)}"

        if _UNIT_HEADING.search(header_text):
            items = [" | ".join(v for v in row.values() if v) for row in rows]
            result.unit_lists.append(UnitList(heading=heading, items=items, source_url=source_url))
        elif _OPERATION_HEADING.search(header_text):
            items = [" | ".join(v for v in row.values() if v) for row in rows]
            result.operation_lists.append(
                OperationList(heading=heading, items=items, source_url=source_url)
            )
        elif _FUNCTION_HEADING.search(header_text):
            for row in rows:
                vals = list(row.values())
                name = vals[0] if vals else ""
                desc = vals[1] if len(vals) > 1 else ""
                if name:
                    result.function_refs.append(
                        FunctionRef(
                            name=name,
                            signature=name,
                            description=desc,
                            source_url=source_url,
                        )
                    )
        elif _FEATURE_HEADING.search(header_text):
            result.feature_tables.append(
                FeatureTable(heading=heading, rows=rows, source_url=source_url)
            )
        else:
            result.feature_tables.append(
                FeatureTable(heading=heading, rows=rows, source_url=source_url)
            )


def _extract_lists(soup: BeautifulSoup, source_url: str, result: DocStructures) -> None:
    """Extract structured ``<ul>``/``<ol>`` lists classified by heading."""
    for tag in soup.find_all(["ul", "ol"]):
        if not isinstance(tag, Tag):
            continue
        # Skip nav/menu lists.
        parent = tag.parent
        if parent and isinstance(parent, Tag):
            if parent.name in {"nav", "footer", "header", "aside"}:
                continue

        items: list[str] = []
        for item in tag.find_all("li", recursive=False):
            text = item.get_text(separator=" ").strip()
            if text:
                items.append(text)

        if len(items) < 3:
            continue

        heading = _nearest_heading(tag)
        if not heading:
            continue

        if _OPERATION_HEADING.search(heading):
            result.operation_lists.append(
                OperationList(heading=heading, items=items, source_url=source_url)
            )
        elif _UNIT_HEADING.search(heading):
            result.unit_lists.append(UnitList(heading=heading, items=items, source_url=source_url))
        elif _FEATURE_HEADING.search(heading):
            result.feature_tables.append(
                FeatureTable(
                    heading=heading,
                    rows=[{"item": item} for item in items],
                    source_url=source_url,
                )
            )


def _extract_dl(soup: BeautifulSoup, source_url: str, result: DocStructures) -> None:
    """Extract ``<dl>`` definition lists as function refs or feature entries."""
    for dl in soup.find_all("dl"):
        if not isinstance(dl, Tag):
            continue

        heading = _nearest_heading(dl)
        terms = dl.find_all("dt")
        defs = dl.find_all("dd")

        if not terms:
            continue

        if _FUNCTION_HEADING.search(heading) or any(
            _SIGNATURE_RE.search(t.get_text()) for t in terms
        ):
            for dt, dd in itertools.zip_longest(terms, defs):
                name_text = dt.get_text(separator=" ").strip()
                desc_text = dd.get_text(separator=" ").strip() if dd else ""
                sig_match = _SIGNATURE_RE.search(name_text)
                name = sig_match.group(1) if sig_match else name_text
                result.function_refs.append(
                    FunctionRef(
                        name=name,
                        signature=name_text,
                        description=desc_text,
                        source_url=source_url,
                    )
                )
        elif _OPERATION_HEADING.search(heading):
            items = [dt.get_text(separator=" ").strip() for dt in terms]
            result.operation_lists.append(
                OperationList(heading=heading, items=items, source_url=source_url)
            )
        elif _UNIT_HEADING.search(heading):
            items = [dt.get_text(separator=" ").strip() for dt in terms]
            result.unit_lists.append(UnitList(heading=heading, items=items, source_url=source_url))


def _extract_code_signatures(soup: BeautifulSoup, source_url: str, result: DocStructures) -> None:
    """Extract function signatures from ``<code>`` elements in API docs."""
    for code in soup.find_all("code"):
        if not isinstance(code, Tag):
            continue
        # Skip code blocks inside <pre> (handled by doc_examples).
        if code.find_parent("pre"):
            continue
        text = code.get_text().strip()
        if not text or len(text) > 200:
            continue
        sig_match = _SIGNATURE_RE.search(text)
        if not sig_match:
            continue

        # Must be in a heading, dt, or standalone paragraph context.
        parent = code.parent
        if not parent or not isinstance(parent, Tag):
            continue
        if parent.name not in {
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "dt",
            "p",
            "li",
            "td",
        }:
            continue

        name = sig_match.group(1)
        # Look for description in the next sibling or dd.
        desc = ""
        if parent.name == "dt":
            dd = parent.find_next_sibling("dd")
            if dd and isinstance(dd, Tag):
                desc = dd.get_text(separator=" ").strip()
        elif parent.name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            nxt = parent.find_next_sibling()
            if nxt and isinstance(nxt, Tag) and nxt.name == "p":
                desc = nxt.get_text(separator=" ").strip()

        # Avoid duplicates from _extract_dl.
        if not any(r.name == name and r.signature == text for r in result.function_refs):
            result.function_refs.append(
                FunctionRef(
                    name=name,
                    signature=text,
                    description=desc,
                    source_url=source_url,
                )
            )
