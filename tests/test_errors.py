"""Canonical MCP error envelope (lore_core.errors)."""

from __future__ import annotations

from lore_core.errors import (
    NO_VAULT,
    NO_WIKIS,
    NOTE_NOT_FOUND,
    SOURCE_NOT_FOUND,
    WIKI_NOT_FOUND,
    mcp_error,
)


def test_envelope_shape_minimum():
    assert mcp_error("foo", "bar") == {"error": {"code": "foo", "message": "bar"}}


def test_envelope_with_next_hint():
    result = mcp_error("foo", "bar", next_="run lore init")
    assert result == {
        "error": {"code": "foo", "message": "bar", "next": "run lore init"},
    }


def test_canonical_codes_are_strings():
    """Constants are plain strings the MCP server can ship to clients."""
    for code in (NO_VAULT, NO_WIKIS, WIKI_NOT_FOUND, SOURCE_NOT_FOUND, NOTE_NOT_FOUND):
        assert isinstance(code, str)
        assert code  # non-empty


def test_no_import_cycle_with_lore_mcp():
    """lore_core.errors must not import from lore_mcp (would create a cycle)."""
    import re
    import lore_core.errors as errors_module
    src = open(errors_module.__file__).read()
    # docstrings may mention lore_mcp; what we want to forbid is actual imports.
    assert not re.search(r"^\s*(import|from)\s+lore_mcp", src, re.MULTILINE)


def test_lore_mcp_re_export_works():
    """The MCP server's _mcp_error alias still resolves to the helper."""
    from lore_mcp.server import _mcp_error
    assert _mcp_error("a", "b") == mcp_error("a", "b")
