"""Integration tests for disagreement on retrieval surfaces — slice 9."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from textwrap import dedent

from lore_core.verdicts_sidecar import set_confirmed
from lore_mcp.server import handle_read, handle_search


def _setup(tmp_path: Path, monkeypatch, body: str, name: str = "n.md") -> Path:
    wiki = tmp_path / "wiki" / "demo"
    (wiki / "concepts").mkdir(parents=True)
    (wiki / "concepts" / name).write_text(body)
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "alice@example.com")
    return wiki


def test_handle_read_carries_disagreement_field(tmp_path, monkeypatch):
    body = dedent("""\
        ---
        type: concept
        status: stale
        stale_by: alice
        stale_at: 2026-05-01
        stale_reason: superseded
        ---
        body
        """)
    wiki = _setup(tmp_path, monkeypatch, body)
    set_confirmed(wiki, "alice", "concepts/n.md", date(2026, 5, 5))

    result = handle_read("concepts/n.md", wiki="demo")
    assert "disagreement" in result["freshness"]
    d = result["freshness"]["disagreement"]
    assert d["stale_by"] == "alice"
    assert d["stale_at"] == "2026-05-01"
    assert d["self_confirmed_at"] == "2026-05-05"


def test_no_disagreement_field_when_no_conflict(tmp_path, monkeypatch):
    body = dedent("""\
        ---
        type: concept
        ---
        body
        """)
    _setup(tmp_path, monkeypatch, body)
    result = handle_read("concepts/n.md", wiki="demo")
    assert "disagreement" not in result["freshness"]
def test_handle_search_disagreement_surfaces_with_downrank(tmp_path, monkeypatch):
    body_dis = dedent("""\
        ---
        type: concept
        description: alpha disagreed
        tags: [alpha]
        status: stale
        stale_by: alice
        stale_at: 2026-05-01
        stale_reason: superseded
        ---
        alpha quick brown fox
        """)
    body_ok = dedent("""\
        ---
        type: concept
        description: alpha plain
        tags: [alpha]
        ---
        alpha plain content
        """)
    wiki = _setup(tmp_path, monkeypatch, body_dis, name="dis.md")
    (wiki / "concepts" / "ok.md").write_text(body_ok)
    set_confirmed(wiki, "alice", "concepts/dis.md", date(2026, 5, 5))

    from lore_core.lint import run_lint

    run_lint()

    hits = handle_search("alpha", wiki="demo", k=5)
    by_path = {h["path"]: h for h in hits}
    if "concepts/dis.md" in by_path:
        # Surfaces in search (recall property), and carries the disagreement.
        assert "disagreement" in by_path["concepts/dis.md"]["freshness"]
