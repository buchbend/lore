"""JSON with comments: read it, and set or delete one key without losing comments.

OpenCode accepts ``opencode.jsonc``; a plain ``json.loads`` + ``json.dumps``
round trip would drop the person's comments. ``set_path`` and
``delete_path`` edit only the text span of the one key they touch.
"""

from __future__ import annotations

import json
import re
from json.decoder import scanstring
from typing import Any

_STR = r'"(?:\\.|[^"\\])*"'
_COMMENT_RE = re.compile(_STR + r"|//[^\n]*|/\*.*?\*/", re.S)
_TRAILING_COMMA_RE = re.compile(_STR + r"|,(\s*[}\]])")
_WS_RE = re.compile(r"(?:\s+|//[^\n]*|/\*.*?\*/)*", re.S)
_LITERAL_RE = re.compile(r"-?[\d.eE+-]+|true|false|null")


def loads(text: str) -> Any:
    """Parse JSONC: drop comments and trailing commas, then ``json.loads``."""
    text = _COMMENT_RE.sub(lambda m: m[0] if m[0].startswith('"') else "", text)
    text = _TRAILING_COMMA_RE.sub(lambda m: m[1] if m[1] is not None else m[0], text)
    return json.loads(text)


def _ws(t: str, i: int) -> int:
    return _WS_RE.match(t, i).end()


def _value(t: str, i: int) -> tuple[int, dict | None]:
    """Skip one value at ``i``; return (end, object node or None)."""
    c = t[i]
    if c == "{":
        return _object(t, i)
    if c == "[":
        i = _ws(t, i + 1)
        while t[i] != "]":
            i = _ws(t, _value(t, i)[0])
            if t[i] == ",":
                i = _ws(t, i + 1)
        return i + 1, None
    if c == '"':
        return scanstring(t, i + 1)[1], None
    m = _LITERAL_RE.match(t, i)
    if not m:
        raise ValueError(f"bad JSONC value at offset {i}")
    return m.end(), None


def _object(t: str, i: int) -> tuple[int, dict]:
    node: dict = {"open": i, "members": []}
    i = _ws(t, i + 1)
    while t[i] != "}":
        key, kend = scanstring(t, i + 1)
        member = {"key": key, "start": i, "comma": None}
        i = _ws(t, kend)
        if t[i] != ":":
            raise ValueError(f"expected ':' at offset {i}")
        member["vstart"] = i = _ws(t, i + 1)
        member["vend"], member["child"] = _value(t, i)
        i = _ws(t, member["vend"])
        if t[i] == ",":
            member["comma"] = i
            i = _ws(t, i + 1)
        node["members"].append(member)
    return i + 1, node


def _root(t: str) -> dict:
    i = _ws(t, 0)
    if i >= len(t) or t[i] != "{":
        raise ValueError("JSONC root must be an object")
    return _object(t, i)[1]


def _indent(t: str, pos: int) -> str:
    start = t.rfind("\n", 0, pos) + 1
    return re.match(r"[ \t]*", t[start:]).group(0)


def _dump(value: Any, indent: str) -> str:
    return json.dumps(value, indent=2).replace("\n", "\n" + indent)


def _nest(keys: list[str], value: Any) -> Any:
    for key in reversed(keys):
        value = {key: value}
    return value


def set_path(text: str, key_path: list[str], value: Any) -> str:
    """Set ``key_path`` to ``value``, creating parents; other text stays."""
    if not text.strip():
        return json.dumps(_nest(key_path, value), indent=2) + "\n"
    node = _root(text)
    for depth, key in enumerate(key_path):
        rest = key_path[depth + 1 :]
        member = next((m for m in node["members"] if m["key"] == key), None)
        if member is None:
            base = _indent(text, node["open"])
            inner = base + "  "
            entry = f"{json.dumps(key)}: {_dump(_nest(rest, value), inner)}"
            if node["members"]:
                at = node["members"][-1]["vend"]
                return text[:at] + ",\n" + inner + entry + text[at:]
            at = node["open"] + 1
            return text[:at] + "\n" + inner + entry + "\n" + base + text[at:]
        if rest and member["child"] is not None:
            node = member["child"]
            continue
        new = _dump(_nest(rest, value), _indent(text, member["start"]))
        return text[: member["vstart"]] + new + text[member["vend"] :]
    return text


def delete_path(text: str, key_path: list[str]) -> str:
    """Remove ``key_path`` and its comma; a missing key leaves the text alone."""
    if not text.strip():
        return text
    node = _root(text)
    for depth, key in enumerate(key_path):
        members = node["members"]
        idx = next((n for n, m in enumerate(members) if m["key"] == key), None)
        if idx is None:
            return text
        member = members[idx]
        if depth < len(key_path) - 1:
            if member["child"] is None:
                return text
            node = member["child"]
            continue
        if member["comma"] is not None:
            start, end = member["start"], member["comma"] + 1
        elif idx and members[idx - 1]["comma"] is not None:
            start, end = members[idx - 1]["comma"], member["vend"]
        else:
            start, end = member["start"], member["vend"]
        line_start = text.rfind("\n", 0, start) + 1
        eol = text.find("\n", end)
        eol = len(text) if eol == -1 else eol
        if not text[line_start:start].strip() and not text[end:eol].strip():
            start, end = line_start, min(eol + 1, len(text))
        return text[:start] + text[end:]
    return text
