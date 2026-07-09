"""Multi-language symbol extraction via tree-sitter tag queries (#166).

Optional layer: only active when the ``lore[codemap]`` extra
(``tree-sitter`` + ``tree-sitter-language-pack``) is installed. The import is
guarded so a base install never touches tree-sitter and the base generator's
Python-only symbol layer keeps working unchanged (see ``codemap/__init__.py``,
which imports this module lazily and checks :data:`AVAILABLE` first).

Each supported grammar gets one small tag query (aider's repo-map tag-query
approach is the conceptual prior art; no code copied) that captures a
definition's name node as ``@name`` alongside one "kind" capture
(``@function``, ``@class``, ...) on the enclosing definition node. A query
match with no kind capture, or no name capture, is skipped.

Vue is a special case: the ``vue`` grammar itself treats ``<script>`` content
as opaque ``raw_text`` (it does not parse the script body), so Vue files are
handled by re-running the TypeScript query over the extracted script text,
with line numbers offset by the script block's position in the file.
"""

from __future__ import annotations

from pathlib import Path

from lore_core.codemap import Symbol

try:
    from tree_sitter import Query, QueryCursor
    from tree_sitter_language_pack import get_language, get_parser

    AVAILABLE = True
except ImportError:  # pragma: no cover - exercised via degradation test
    AVAILABLE = False

# File extension -> tree-sitter-language-pack grammar name.
EXTENSION_LANGUAGES = {
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".vue": "vue",
    ".rs": "rust",
    ".jl": "julia",
    ".html": "html",
    ".htm": "html",
}

_JS_QUERY = """
(function_declaration name: (identifier) @name) @function
(method_definition name: (property_identifier) @name) @method
(class_declaration name: (identifier) @name) @class
"""

# TypeScript's class_declaration name is a type_identifier (JS uses a plain
# identifier), so this can't just extend _JS_QUERY with an extra pattern.
_TS_QUERY = """
(function_declaration name: (identifier) @name) @function
(method_definition name: (property_identifier) @name) @method
(class_declaration name: (type_identifier) @name) @class
(interface_declaration name: (type_identifier) @name) @interface
"""

_RUST_QUERY = """
(function_item name: (identifier) @name) @function
(struct_item name: (type_identifier) @name) @struct
(trait_item name: (type_identifier) @name) @trait
"""

_JULIA_QUERY = """
(function_definition (signature (call_expression (identifier) @name))) @function
(struct_definition (type_head (identifier) @name)) @struct
(module_definition (identifier) @name) @module
"""

# HTML has no function/class definitions; the closest analogue is an element
# carrying an ``id`` (a named, referenceable anchor/component root).
_HTML_QUERY = """
(element
  (start_tag
    (tag_name) @tag
    (attribute
      (attribute_name) @attr_name
      (quoted_attribute_value (attribute_value) @name)))
  (#eq? @attr_name "id")) @element
"""

_QUERIES = {
    "javascript": _JS_QUERY,
    "typescript": _TS_QUERY,
    "tsx": _TS_QUERY,
    "rust": _RUST_QUERY,
    "julia": _JULIA_QUERY,
    "html": _HTML_QUERY,
}

# Captures that are metadata, not a kind label, when found alongside @name.
_NON_KIND_CAPTURES = {"name", "tag", "attr_name"}


def language_for(relpath: str) -> str | None:
    """Return the tree-sitter grammar name for *relpath*, or None if unsupported."""
    return EXTENSION_LANGUAGES.get(Path(relpath).suffix.lower())


def extract_symbols(relpath: str, source: str, language: str) -> list[Symbol]:
    """Extract tag-query symbols from one *language* source file.

    Mirrors the base Python ``extract_symbols``: any failure (missing
    grammar, parse error, malformed query match) yields an empty list rather
    than raising, so one unsupported or broken file never breaks the map.
    """
    if not AVAILABLE:
        return []
    if language == "vue":
        return _extract_vue(relpath, source)
    query_src = _QUERIES.get(language)
    if query_src is None:
        return []
    try:
        return _run_query(relpath, source.encode("utf-8"), language, query_src, line_offset=0)
    except Exception:
        return []


def _extract_vue(relpath: str, source: str) -> list[Symbol]:
    """Delegate each ``<script>`` block's raw text to the TypeScript query."""
    try:
        tree = get_parser("vue").parse(source.encode("utf-8"))
    except Exception:
        return []
    symbols: list[Symbol] = []
    for node in _walk(tree.root_node):
        if node.type != "script_element":
            continue
        raw = next((c for c in node.children if c.type == "raw_text"), None)
        if raw is None:
            continue
        try:
            symbols.extend(
                _run_query(
                    relpath,
                    raw.text,
                    "typescript",
                    _TS_QUERY,
                    line_offset=raw.start_point[0],
                )
            )
        except Exception:
            continue
    return symbols


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _run_query(
    relpath: str, source_bytes: bytes, language: str, query_src: str, *, line_offset: int
) -> list[Symbol]:
    tree = get_parser(language).parse(source_bytes)
    query = Query(get_language(language), query_src)
    cursor = QueryCursor(query)
    symbols: list[Symbol] = []
    for _pattern_idx, match in cursor.matches(tree.root_node):
        name_nodes = match.get("name")
        if not name_nodes:
            continue
        kind = next((k for k in match if k not in _NON_KIND_CAPTURES), None)
        if kind is None:
            continue
        def_node = match[kind][0]
        name = name_nodes[0].text.decode("utf-8", errors="replace")
        lineno = def_node.start_point[0] + 1 + line_offset
        symbols.append(Symbol(name, name, kind, relpath, lineno))
    return symbols
