"""Tests for the ``lore_verdict`` MCP tool — slice 5 of PRD #65."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from lore_core.schema import parse_frontmatter
from lore_mcp.server import handle_read, handle_verdict


def _setup(tmp_path: Path, monkeypatch, body: str) -> Path:
    wiki = tmp_path / "wiki" / "demo"
    (wiki / "concepts").mkdir(parents=True)
    (wiki / "concepts" / "n.md").write_text(body)
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "alice@example.com")
    return wiki


def test_verdict_stale_writes_four_fields_and_returns_signal(tmp_path, monkeypatch):
    body = dedent("""\
        ---
        type: concept
        description: thing
        ---
        body
        """)
    wiki = _setup(tmp_path, monkeypatch, body)
    result = handle_verdict(
        wiki="demo", note="concepts/n.md", verdict="stale", reason="superseded"
    )
    assert result.get("verdict") == "stale"
    assert result["freshness"]["status"] == "stale-candidate"
    assert result["freshness"]["cause"] == "authored_marker"
    fm = parse_frontmatter((wiki / "concepts" / "n.md").read_text())
    assert fm["status"] == "stale"
    assert fm["stale_reason"] == "superseded"
    assert fm["stale_by"] == "alice"
    assert "stale_at" in fm


def test_verdict_stale_without_reason_returns_error(tmp_path, monkeypatch):
    body = dedent("""\
        ---
        type: concept
        ---
        body
        """)
    _setup(tmp_path, monkeypatch, body)
    result = handle_verdict(wiki="demo", note="concepts/n.md", verdict="stale")
    assert "error" in result
    assert result["error"]["code"] == "reason_required"


def test_verdict_confirm_writes_personal_sidecar(tmp_path, monkeypatch):
    """Slice 6 replaced slice 5's `confirm` stub with the sidecar write."""
    body = dedent("""\
        ---
        type: concept
        supersede_candidate: "[[newer]]"
        ---
        body
        """)
    _setup(tmp_path, monkeypatch, body)
    result = handle_verdict(
        wiki="demo", note="concepts/n.md", verdict="confirm"
    )
    assert "error" not in result
    assert result.get("verdict") == "confirm"
    assert "confirmed_at" in result
    # Sidecar suppression of soft markers fires immediately (mtime is "now",
    # but the mtime guard in compute_freshness compares mtime <= today's
    # confirmed_at — both are today, so the >= check passes).
    assert result["freshness"]["status"] == "confirmed"


def test_verdict_unknown_value_returns_error(tmp_path, monkeypatch):
    body = "---\ntype: concept\n---\nbody\n"
    _setup(tmp_path, monkeypatch, body)
    result = handle_verdict(wiki="demo", note="concepts/n.md", verdict="bogus")
    assert "error" in result
    assert result["error"]["code"] == "invalid_verdict"


def test_verdict_unknown_wiki_returns_error(tmp_path, monkeypatch):
    body = "---\ntype: concept\n---\nbody\n"
    _setup(tmp_path, monkeypatch, body)
    result = handle_verdict(wiki="other", note="x", verdict="stale", reason="x")
    assert "error" in result
    assert result["error"]["code"] == "wiki_not_found"


def test_verdict_unknown_note_returns_error(tmp_path, monkeypatch):
    body = "---\ntype: concept\n---\nbody\n"
    _setup(tmp_path, monkeypatch, body)
    result = handle_verdict(
        wiki="demo", note="missing-slug", verdict="stale", reason="x"
    )
    assert "error" in result


def test_verdict_after_stale_search_response_reflects_stale(tmp_path, monkeypatch):
    body = "---\ntype: concept\ndescription: t\n---\nbody\n"
    _setup(tmp_path, monkeypatch, body)
    handle_verdict(
        wiki="demo", note="concepts/n.md", verdict="stale", reason="X"
    )
    after = handle_read("concepts/n.md", wiki="demo")
    assert after["freshness"]["status"] == "stale-candidate"
    assert after["freshness"]["cause"] == "authored_marker"


def test_verdict_clear_stale_reverses_stale(tmp_path, monkeypatch):
    body = "---\ntype: concept\ndescription: t\n---\nbody\n"
    wiki = _setup(tmp_path, monkeypatch, body)
    handle_verdict(
        wiki="demo", note="concepts/n.md", verdict="stale", reason="X"
    )
    fm_after = parse_frontmatter((wiki / "concepts" / "n.md").read_text())
    assert fm_after["status"] == "stale"

    result = handle_verdict(
        wiki="demo", note="concepts/n.md", verdict="clear-stale"
    )
    assert result.get("verdict") == "clear-stale"
    assert result["freshness"]["status"] == "confirmed"
    fm_after = parse_frontmatter((wiki / "concepts" / "n.md").read_text())
    assert "status" not in fm_after
    assert "stale_reason" not in fm_after


def test_verdict_stale_idempotent(tmp_path, monkeypatch):
    body = "---\ntype: concept\n---\nbody\n"
    _setup(tmp_path, monkeypatch, body)
    r1 = handle_verdict(
        wiki="demo", note="concepts/n.md", verdict="stale", reason="X"
    )
    assert "error" not in r1
    r2 = handle_verdict(
        wiki="demo", note="concepts/n.md", verdict="stale", reason="X"
    )
    # Second identical call is idempotent (same handle, same reason, same date).
    assert "error" not in r2


def test_verdict_stale_refuses_to_overwrite_different_reason(tmp_path, monkeypatch):
    body = "---\ntype: concept\n---\nbody\n"
    _setup(tmp_path, monkeypatch, body)
    handle_verdict(
        wiki="demo", note="concepts/n.md", verdict="stale", reason="reason A"
    )
    result = handle_verdict(
        wiki="demo", note="concepts/n.md", verdict="stale", reason="reason B"
    )
    assert "error" in result
    assert result["error"]["code"] == "stale_write_refused"
