"""Shared BeautifulSoup helpers for coercing attribute values to str.

``bs4.Tag.get(name)`` returns ``str | AttributeValueList | None`` in
recent bs4 stubs. ``AttributeValueList`` is the multi-valued shape
the parser produces for attributes like ``class`` and ``rel``, which
can legitimately carry multiple tokens. Most call sites in this
package treat the attribute as a single string and don't care about
the multi-token case; uniformly coercing to ``str`` at the boundary
keeps the type narrow and the call sites readable.
"""

from __future__ import annotations

from bs4 import Tag


def str_attr(tag: Tag, name: str, default: str = "") -> str:
    """Return ``tag``'s ``name`` attribute as a single string.

    Multi-valued attributes are joined with a single space; missing
    attributes (and the value ``None``) become ``default``. Use this
    at the boundary before any string operation; the callers can then
    rely on the return type without further narrowing.
    """
    value = tag.get(name, default)
    if isinstance(value, list):
        return " ".join(str(item) for item in value) if value else default
    if value is None:
        return default
    return str(value)


def class_list(tag: Tag) -> list[str]:
    """Return ``tag``'s ``class`` attribute as a list of strings.

    bs4 returns the class attribute as a multi-valued list by default
    but can also surface it as a single string when the document uses
    a non-default parser. Normalize to ``list[str]`` for the call
    sites that iterate over class tokens.
    """
    value = tag.get("class")
    if value is None:
        return []
    if isinstance(value, str):
        return value.split()
    return [str(item) for item in value]


__all__ = ["class_list", "str_attr"]
