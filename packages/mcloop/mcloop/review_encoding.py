"""Compact generated JSON reports without dropping their parsed values."""

from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal


def encoded(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


class ReportEncoding:
    def __init__(self, changes: dict):
        self.fields: dict[str, list[str]] = {}
        self.strings: dict[str, str] = {}
        self.changes = dict(changes)
        schemas: dict[tuple[str, ...], str] = {}
        counts: Counter[str] = Counter()

        def records(value):
            if isinstance(value, Decimal):
                return {"$number": str(value)}
            if isinstance(value, dict):
                keys = tuple(sorted(value))
                identity = schemas.setdefault(keys, f"J{len(schemas) + 1}")
                self.fields[identity] = list(keys)
                return {identity: [records(value[key]) for key in keys]}
            if isinstance(value, list):
                return [records(item) for item in value]
            if isinstance(value, str):
                counts[value] += 1
            return value

        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError("Duplicate JSON key")
                result[key] = value
            return result

        def invalid_constant(value):
            raise ValueError(value)

        for name, content in changes.items():
            if not name.startswith("evidence/") or not isinstance(content, str):
                continue
            try:
                value = json.loads(
                    content,
                    object_pairs_hook=pairs,
                    parse_constant=invalid_constant,
                    parse_float=Decimal,
                )
            except (ValueError, RecursionError):
                continue
            if not isinstance(value, (dict, list)):
                continue
            self.changes[name] = {"format": "json_records", "value": records(value)}

        shared = {
            value: f"T{index + 1}"
            for index, (value, count) in enumerate(counts.items())
            if count > 1 and (len(encoded(value)) - 20) * (count - 1) > 40
        }
        self.strings = {identity: value for value, identity in shared.items()}

        def strings(value):
            if isinstance(value, str):
                return {"$text": shared[value]} if value in shared else value
            if isinstance(value, list):
                return [strings(item) for item in value]
            if isinstance(value, dict):
                if "$number" in value:
                    return value
                return {key: strings(item) for key, item in value.items()}
            return value

        for name, content in self.changes.items():
            if isinstance(content, dict) and content.get("format") == "json_records":
                self.changes[name] = dict(content, value=strings(content["value"]))

    def pack(self, names: list[str]) -> tuple[dict, dict]:
        tree: dict = {}
        fields = set()
        strings = set()

        def references(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key == "$text":
                        strings.add(item)
                    elif key in self.fields:
                        fields.add(key)
                    references(item)
            elif isinstance(value, list):
                for item in value:
                    references(item)

        for name in names:
            content = self.changes[name]
            if isinstance(content, dict) and content.get("format") == "json_records":
                references(content["value"])
            node = tree
            segments = name.split("/")
            for segment in segments[:-1]:
                node = node.setdefault(segment, {})
                if isinstance(node, list):
                    if len(node) == 1:
                        node.append({})
                    node = node[1]
            children = node.get(segments[-1])
            node[segments[-1]] = [content, children] if children is not None else [content]
        return tree, {
            "instruction": (
                "Changed files use a relative path tree. Join directory keys with '/'; "
                "each leaf array contains that file's content. An optional second array "
                "element holds children when a deleted file becomes a directory. JSON reports "
                "use json_records: each {Jid:[values]} object pairs values with fields[Jid] "
                "in order. {$text:Tid} means strings[Tid]. Other arrays retain their order. "
                "{$number:literal} retains an exact JSON decimal number. "
                "All JSON fields and values remain present. Original text and hashes are "
                "retained in the review receipt. Source files remain literal text."
            ),
            "fields": {key: self.fields[key] for key in sorted(fields)},
            "strings": {key: self.strings[key] for key in sorted(strings)},
        }


def unpack_changes(part: dict) -> dict:
    """Expand a review part for coverage checks and inspection."""
    if "report_encoding" not in part:
        return dict(part["changed_files"])
    encoding = part["report_encoding"]

    def expand(value):
        if isinstance(value, dict):
            key, items = next(iter(value.items()))
            if key == "$text":
                return encoding["strings"][items]
            if key == "$number":
                return Decimal(items)
            return dict(zip(encoding["fields"][key], map(expand, items), strict=True))
        if isinstance(value, list):
            return [expand(item) for item in value]
        return value

    result = {}

    def walk(node, prefix):
        for name, value in node.items():
            path = prefix + name
            if isinstance(value, dict):
                walk(value, path + "/")
            else:
                content = value[0]
                if isinstance(content, dict) and content.get("format") == "json_records":
                    content = {"format": "json_value", "value": expand(content["value"])}
                result[path] = content
                if len(value) == 2:
                    walk(value[1], path + "/")

    walk(part["changed_files"], "")
    return result
