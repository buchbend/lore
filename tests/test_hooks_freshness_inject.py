"""SessionStart inject filter tests — slice 3 of PRD #65."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from lore_cli.hooks import (
    _filter_session_hints,
    _last_session_hint_with_freshness,
)


def _make_session(
    wiki: Path, name: str, body: str, mtime: float | None = None
) -> Path:
    sess = wiki / "sessions" / "2026" / "05"
    sess.mkdir(parents=True, exist_ok=True)
    p = sess / name
    p.write_text(body)
    if mtime is not None:
        import os

        os.utime(p, (mtime, mtime))
    return p


def test_session_hints_with_freshness_carries_block(tmp_path):
    wiki = tmp_path / "wiki" / "demo"
    wiki.mkdir(parents=True)
    body = dedent("""\
        ---
        type: session
        title: confirmed session
        ---
        body
        """)
    _make_session(wiki, "08-1500-confirmed.md", body)

    hits = _last_session_hint_with_freshness(wiki)
    assert hits
    assert hits[0][2]["status"] == "confirmed"


def test_filter_excludes_status_stale_session(tmp_path):
    wiki = tmp_path / "wiki" / "demo"
    wiki.mkdir(parents=True)
    body_ok = dedent("""\
        ---
        type: session
        title: ok session
        ---
        body
        """)
    body_stale = dedent("""\
        ---
        type: session
        title: stale session
        status: stale
        ---
        body
        """)
    # Order in time: stale is newest, ok is older — newest-first sort puts
    # stale first; the filter must drop it.
    _make_session(wiki, "07-0900-ok.md", body_ok)
    _make_session(wiki, "08-1500-stale.md", body_stale)

    candidates = _last_session_hint_with_freshness(wiki, max_notes=4)
    kept, audit_lines = _filter_session_hints(candidates, max_notes=2)
    kept_slugs = [s for s, _ in kept]
    assert "08-1500-stale" not in kept_slugs
    assert "07-0900-ok" in kept_slugs
    assert any("excluded" in line for line in audit_lines)


def test_filter_downranks_soft_stale_after_confirmed(tmp_path):
    wiki = tmp_path / "wiki" / "demo"
    wiki.mkdir(parents=True)
    body_soft = dedent("""\
        ---
        type: session
        title: soft stale
        supersede_candidate: "[[newer]]"
        ---
        body
        """)
    body_ok = dedent("""\
        ---
        type: session
        title: ok session
        ---
        body
        """)
    # Soft is newest, OK is older. After downrank, ok comes first.
    _make_session(wiki, "07-0900-ok.md", body_ok)
    _make_session(wiki, "08-1500-soft.md", body_soft)

    candidates = _last_session_hint_with_freshness(wiki, max_notes=4)
    kept, audit_lines = _filter_session_hints(candidates, max_notes=4)
    kept_slugs = [s for s, _ in kept]
    # Confirmed comes before soft after the filter, even though soft was newer.
    assert kept_slugs.index("07-0900-ok") < kept_slugs.index("08-1500-soft")
    # Audit logged the downrank.
    assert any("downranked" in line for line in audit_lines)


def test_filter_no_audit_when_all_confirmed(tmp_path):
    wiki = tmp_path / "wiki" / "demo"
    wiki.mkdir(parents=True)
    body = dedent("""\
        ---
        type: session
        title: ok
        ---
        body
        """)
    _make_session(wiki, "08-1500-ok.md", body)
    candidates = _last_session_hint_with_freshness(wiki, max_notes=4)
    _kept, audit_lines = _filter_session_hints(candidates)
    assert audit_lines == []


def test_max_notes_caps_after_filter(tmp_path):
    wiki = tmp_path / "wiki" / "demo"
    wiki.mkdir(parents=True)
    body = dedent("""\
        ---
        type: session
        title: ok-{i}
        ---
        body
        """)
    for i in range(5):
        _make_session(wiki, f"0{i+1}-1500-ok-{i}.md", body)
    candidates = _last_session_hint_with_freshness(wiki, max_notes=4)
    kept, _ = _filter_session_hints(candidates, max_notes=2)
    assert len(kept) == 2
