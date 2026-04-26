"""MCP error envelope contract — Phase 5.

Tool handlers return ``{"error": {"code", "message", "next"?}}`` so MCP
clients (Claude, Cursor, etc.) can branch on ``code`` instead of
parsing English. Pre-Phase-5 the shape was ``{"error": "string"}``,
inconsistent across handlers. This file pins the new contract down.

Note: the JSON-RPC dispatcher's protocol-level errors (``-32xxx`` codes
at the bottom of ``server.py``) use the JSON-RPC standard shape and
are *not* this envelope — different layer, different contract.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lore_mcp.server import (
    _mcp_error,
    handle_catalog,
    handle_index,
    handle_read,
    handle_wikilinks,
)


# ---- helper itself ----


def test_mcp_error_helper_basic() -> None:
    err = _mcp_error("wiki_not_found", "wiki not found: foo")
    assert err == {"error": {"code": "wiki_not_found", "message": "wiki not found: foo"}}


def test_mcp_error_helper_with_next_hint() -> None:
    err = _mcp_error("catalog_missing", "no _catalog.json", next_="run lore lint")
    assert err == {
        "error": {
            "code": "catalog_missing",
            "message": "no _catalog.json",
            "next": "run lore lint",
        }
    }


def test_mcp_error_helper_omits_empty_next() -> None:
    """Empty/None next_ should not pollute the envelope."""
    assert "next" not in _mcp_error("x", "y")["error"]
    assert "next" not in _mcp_error("x", "y", next_=None)["error"]
    assert "next" not in _mcp_error("x", "y", next_="")["error"]


# ---- handler integration ----


@pytest.fixture()
def empty_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A vault with one wiki and no catalog/index/notes — exercises the
    'wiki found but the artifact you asked for is missing' paths."""
    wiki = tmp_path / "wiki" / "private"
    wiki.mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    return tmp_path


def _assert_envelope(result: dict, code: str, *, want_next: bool = False) -> None:
    assert "error" in result, f"missing error key: {result}"
    err = result["error"]
    assert isinstance(err, dict), f"error must be dict envelope, got {type(err)}"
    assert err["code"] == code, f"expected code={code!r}, got {err['code']!r}"
    assert isinstance(err["message"], str) and err["message"], "message empty"
    if want_next:
        assert err.get("next"), f"expected 'next' hint for code={code!r}"


def test_handle_index_missing_catalog_envelope(empty_vault: Path) -> None:
    result = handle_index(wiki="private")
    _assert_envelope(result, "catalog_missing", want_next=True)
    assert "lore lint" in result["error"]["next"]


def test_handle_catalog_missing_envelope(empty_vault: Path) -> None:
    result = handle_catalog(wiki="private")
    _assert_envelope(result, "catalog_missing", want_next=True)


def test_handle_wikilinks_missing_catalog_envelope(empty_vault: Path) -> None:
    result = handle_wikilinks(note="anything", wiki="private")
    _assert_envelope(result, "catalog_missing", want_next=True)


def test_handle_read_unknown_wiki_envelope(empty_vault: Path) -> None:
    result = handle_read(path="any", wiki="does-not-exist")
    _assert_envelope(result, "wiki_not_found", want_next=True)
    assert "lore status" in result["error"]["next"]


def test_handle_read_path_escape_envelope(empty_vault: Path) -> None:
    """Security-critical: path-escape returns a structured error so a
    misbehaving client can't trivially keep retrying past the guard."""
    result = handle_read(path="../../etc/passwd", wiki="private")
    _assert_envelope(result, "path_escape", want_next=False)
