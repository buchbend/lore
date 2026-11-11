"""Tests for the ``lore_pending_verdicts`` MCP handler.

End-to-end coverage of the picker-side surface: catalog walk plus
per-note frontmatter read plus sidecar resolution, returned in the
``lore.pending_verdicts/1`` envelope.
"""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

from lore_mcp.server import handle_pending_verdicts


def _setup_wiki(tmp_path: Path, monkeypatch, wiki_name: str = "demo") -> Path:
    wiki = tmp_path / "wiki" / wiki_name
    wiki.mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "alice@example.com")
    return wiki


def _write_note(wiki: Path, rel: str, frontmatter: dict) -> None:
    p = wiki / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for k, v in frontmatter.items():
        if isinstance(v, str):
            lines.append(f"{k}: '{v}'")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("body")
    p.write_text("\n".join(lines))


def _write_catalog(wiki: Path, entries: list[dict], orphan_set: list[str] | None = None) -> None:
    data: dict = {"sections": {"notes": entries}}
    if orphan_set is not None:
        data["orphan_set"] = orphan_set
    (wiki / "_catalog.json").write_text(json.dumps(data))


# ---------------------------------------------------------------------------
# Empty + happy-path
# ---------------------------------------------------------------------------


def test_no_pending_returns_empty_envelope(tmp_path, monkeypatch):
    wiki = _setup_wiki(tmp_path, monkeypatch)
    _write_note(wiki, "n.md", {"type": "concept"})
    _write_catalog(wiki, [{"path": "n.md"}])

    result = handle_pending_verdicts(wiki="demo")
    assert result == {
        "schema": "lore.pending_verdicts/1",
        "wiki": "demo",
        "pending": [],
        "count": 0,
        "capped": False,
    }


def test_one_authored_marker_entry(tmp_path, monkeypatch):
    wiki = _setup_wiki(tmp_path, monkeypatch)
    _write_note(
        wiki,
        "concepts/old.md",
        {
            "status": "stale",
            "stale_by": "alice",
            "stale_at": "2026-05-01",
        },
    )
    _write_catalog(wiki, [{"path": "concepts/old.md", "status": "stale"}])

    result = handle_pending_verdicts(wiki="demo")
    assert result["count"] == 1
    entry = result["pending"][0]
    assert entry["path"] == "concepts/old.md"
    assert entry["slug"] == "old"
    assert entry["cause"] == "authored_marker"
    assert entry["reason"] == "marked stale"
    assert entry["disagreement"] is None


# ---------------------------------------------------------------------------
# Disagreement wires through into the response
# ---------------------------------------------------------------------------


def test_disagreement_block_surfaces_in_response(tmp_path, monkeypatch):
    wiki = _setup_wiki(tmp_path, monkeypatch)
    _write_note(
        wiki,
        "n.md",
        {
            "status": "stale",
            "stale_by": "bob",
            "stale_at": "2026-05-01",
            "stale_reason": "wrong",
        },
    )
    # `alice` is the current user (via GIT_AUTHOR_EMAIL); record her
    # personal confirm AFTER the stale verdict.
    sidecar_dir = wiki / "_verdicts"
    sidecar_dir.mkdir()
    (sidecar_dir / "alice.json").write_text(
        json.dumps({"confirmed": {"n.md": "2026-05-05"}})
    )
    _write_catalog(wiki, [{"path": "n.md", "status": "stale"}])

    result = handle_pending_verdicts(wiki="demo")
    assert result["count"] == 1
    entry = result["pending"][0]
    assert entry["confirmed_at"] == "2026-05-05"
    assert entry["disagreement"] == {
        "stale_by": "bob",
        "stale_at": "2026-05-01",
        "stale_reason": "wrong",
        "self_confirmed_at": "2026-05-05",
    }


# ---------------------------------------------------------------------------
# Wiki auto-resolution
# ---------------------------------------------------------------------------


def test_auto_resolve_single_wiki_when_arg_omitted(tmp_path, monkeypatch):
    wiki = _setup_wiki(tmp_path, monkeypatch, wiki_name="only")
    _write_note(wiki, "n.md", {"status": "stale"})
    _write_catalog(wiki, [{"path": "n.md", "status": "stale"}])

    result = handle_pending_verdicts()
    assert result["wiki"] == "only"
    assert result["count"] == 1


def test_explicit_wiki_arg_wins_over_auto(tmp_path, monkeypatch):
    # Two wikis — auto would be ambiguous, but explicit arg resolves it.
    _setup_wiki(tmp_path, monkeypatch, wiki_name="a")
    wiki_b = _setup_wiki(tmp_path, monkeypatch, wiki_name="b")
    _write_note(wiki_b, "n.md", {"status": "stale", "stale_at": "2026-05-01", "stale_by": "alice"})
    _write_catalog(wiki_b, [{"path": "n.md", "status": "stale"}])
    # `a` has no catalog → empty
    result = handle_pending_verdicts(wiki="b")
    assert result["wiki"] == "b"
    assert result["count"] == 1


def test_unknown_wiki_returns_error(tmp_path, monkeypatch):
    _setup_wiki(tmp_path, monkeypatch)
    result = handle_pending_verdicts(wiki="nonexistent")
    assert "error" in result
    assert result["error"]["code"] == "wiki_not_found"


# ---------------------------------------------------------------------------
# Sort ordering preserved through the handler
# ---------------------------------------------------------------------------


def test_sort_order_disagreement_authored_orphan(tmp_path, monkeypatch):
    wiki = _setup_wiki(tmp_path, monkeypatch)
    # Disagreement entry
    _write_note(
        wiki,
        "z-disagree.md",
        {
            "status": "stale",
            "stale_by": "bob",
            "stale_at": "2026-04-01",
            "stale_reason": "x",
        },
    )
    sidecar_dir = wiki / "_verdicts"
    sidecar_dir.mkdir()
    (sidecar_dir / "alice.json").write_text(
        json.dumps({"confirmed": {"z-disagree.md": "2026-05-01"}})
    )
    # Plain authored
    _write_note(
        wiki,
        "m-authored.md",
        {"status": "stale", "stale_at": "2026-05-10", "stale_by": "alice"},
    )
    # Orphan-only
    _write_note(wiki, "a-orphan.md", {})
    _write_catalog(
        wiki,
        [
            {"path": "z-disagree.md", "status": "stale"},
            {"path": "m-authored.md", "status": "stale"},
            {"path": "a-orphan.md"},
        ],
        orphan_set=["a-orphan.md"],
    )

    result = handle_pending_verdicts(wiki="demo")
    paths = [e["path"] for e in result["pending"]]
    assert paths == ["z-disagree.md", "m-authored.md", "a-orphan.md"]
