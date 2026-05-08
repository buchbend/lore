"""End-to-end tests for personal-confirm sidecar wiring — slice 6 of PRD #65."""

from __future__ import annotations

import os
import time
from datetime import date, timedelta
from pathlib import Path
from textwrap import dedent

from lore_core.verdicts_sidecar import set_confirmed
from lore_mcp.server import handle_read, handle_verdict


def _setup(tmp_path: Path, monkeypatch, body: str, name: str = "n.md") -> Path:
    wiki = tmp_path / "wiki" / "demo"
    (wiki / "concepts").mkdir(parents=True)
    p = wiki / "concepts" / name
    p.write_text(body)
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "alice@example.com")
    return wiki


def _set_mtime(path: Path, target: date) -> None:
    ts = time.mktime((target.year, target.month, target.day, 12, 0, 0, 0, 0, -1))
    os.utime(path, (ts, ts))


def test_confirm_suppresses_soft_marker_on_subsequent_read(tmp_path, monkeypatch):
    body = dedent("""\
        ---
        type: concept
        supersede_candidate: "[[newer]]"
        ---
        body
        """)
    wiki = _setup(tmp_path, monkeypatch, body)
    # Make the note older than today so the mtime gate passes after confirm.
    _set_mtime(wiki / "concepts" / "n.md", date.today() - timedelta(days=3))

    handle_verdict(wiki="demo", note="concepts/n.md", verdict="confirm")
    after = handle_read("concepts/n.md", wiki="demo")
    assert after["freshness"]["status"] == "confirmed"
    assert after["freshness"]["confirmed_at"] is not None


def test_confirm_does_not_suppress_status_stale(tmp_path, monkeypatch):
    body = dedent("""\
        ---
        type: concept
        status: stale
        ---
        body
        """)
    wiki = _setup(tmp_path, monkeypatch, body)
    _set_mtime(wiki / "concepts" / "n.md", date.today() - timedelta(days=3))

    handle_verdict(wiki="demo", note="concepts/n.md", verdict="confirm")
    after = handle_read("concepts/n.md", wiki="demo")
    # Hard team-wide marker survives — confirm cannot vouch for team truth.
    assert after["freshness"]["status"] == "stale-candidate"
    assert after["freshness"]["cause"] == "authored_marker"


def test_confirm_does_not_suppress_superseded_by(tmp_path, monkeypatch):
    body = dedent("""\
        ---
        type: concept
        superseded_by: "[[newer]]"
        ---
        body
        """)
    wiki = _setup(tmp_path, monkeypatch, body)
    _set_mtime(wiki / "concepts" / "n.md", date.today() - timedelta(days=3))

    handle_verdict(wiki="demo", note="concepts/n.md", verdict="confirm")
    after = handle_read("concepts/n.md", wiki="demo")
    assert after["freshness"]["status"] == "stale-candidate"


def test_confirm_after_edit_no_longer_suppresses(tmp_path, monkeypatch):
    """Edit-then-confirm: bumping mtime after a confirm invalidates suppression."""
    body = dedent("""\
        ---
        type: concept
        supersede_candidate: "[[newer]]"
        ---
        body
        """)
    wiki = _setup(tmp_path, monkeypatch, body)
    note_path = wiki / "concepts" / "n.md"
    # Note was old when confirmed.
    seven_days_ago = date.today() - timedelta(days=7)
    _set_mtime(note_path, seven_days_ago)
    set_confirmed(wiki, "alice", "concepts/n.md", seven_days_ago + timedelta(days=1))
    # Now simulate editing the note: bump mtime to today.
    _set_mtime(note_path, date.today())

    after = handle_read("concepts/n.md", wiki="demo")
    assert after["freshness"]["status"] == "stale-candidate"


def test_confirm_outside_recency_window_no_longer_suppresses(tmp_path, monkeypatch):
    body = dedent("""\
        ---
        type: concept
        supersede_candidate: "[[newer]]"
        ---
        body
        """)
    wiki = _setup(tmp_path, monkeypatch, body)
    note_path = wiki / "concepts" / "n.md"
    long_ago = date.today() - timedelta(days=90)
    _set_mtime(note_path, long_ago)
    set_confirmed(wiki, "alice", "concepts/n.md", long_ago + timedelta(days=1))

    after = handle_read("concepts/n.md", wiki="demo")
    # Confirm older than the 14-day window — soft marker re-flags.
    assert after["freshness"]["status"] == "stale-candidate"


def test_confirm_response_carries_confirmed_at(tmp_path, monkeypatch):
    body = "---\ntype: concept\n---\nbody\n"
    _setup(tmp_path, monkeypatch, body)
    result = handle_verdict(
        wiki="demo", note="concepts/n.md", verdict="confirm"
    )
    assert result["confirmed_at"] == date.today().isoformat()
    assert result["freshness"]["confirmed_at"] is not None
