"""Integration tests for disagreement on retrieval surfaces — slice 9."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from textwrap import dedent

from lore_cli.hooks import _filter_session_hints
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


def test_disagreement_excluded_from_inject_filter(tmp_path):
    """Slice 3 + 9: a hit with disagreement is hard-excluded from inject."""
    candidates = [
        (
            "stale-then-confirmed",
            "title",
            {
                "status": "stale-candidate",
                "cause": "authored_marker",
                "reason": "marked stale",
                "confirmed_at": "2026-05-05",
                "disagreement": {
                    "stale_by": "alice",
                    "stale_at": "2026-05-01",
                    "stale_reason": "X",
                    "self_confirmed_at": "2026-05-05",
                },
            },
        ),
    ]
    kept, audit_lines = _filter_session_hints(candidates, max_notes=2)
    assert kept == []
    assert any("excluded" in line for line in audit_lines)


def test_disagreement_excluded_even_when_status_confirmed(tmp_path):
    """When personal confirm suppresses to confirmed but disagreement
    is set (because slice-9 detector saw the conflict), inject still
    excludes — defer to team-stale until user resolves."""
    candidates = [
        (
            "x",
            "t",
            {
                "status": "confirmed",
                "cause": "none",
                "reason": None,
                "confirmed_at": "2026-05-05",
                "disagreement": {
                    "stale_by": "alice",
                    "stale_at": "2026-05-01",
                    "stale_reason": "X",
                    "self_confirmed_at": "2026-05-05",
                },
            },
        ),
    ]
    kept, _ = _filter_session_hints(candidates, max_notes=2)
    assert kept == []


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
