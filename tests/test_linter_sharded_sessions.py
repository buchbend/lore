"""Linter drops the sessions/ scan whole (PRD 0013).

Nothing writes a session note since the compose pipeline was retired, so
`discover_notes` no longer walks `<wiki>/sessions/` — flat layout or
sharded (team-mode) layout alike.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lore_core.lint import discover_notes


@pytest.fixture
def wiki_with_sessions(tmp_path) -> Path:
    w = tmp_path / "ccat"
    (w / "sessions").mkdir(parents=True)
    # Flat layout (solo mode)
    (w / "sessions" / "2026-04-01-flat-session.md").write_text("x")
    # Sharded layout (team mode)
    (w / "sessions" / "buchbend").mkdir()
    (w / "sessions" / "buchbend" / "2026-04-02-sharded-session.md").write_text("y")
    return w


def test_discover_notes_ignores_flat_sessions(wiki_with_sessions):
    notes = discover_notes(wiki_with_sessions)
    names = {n.name for n in notes}
    assert "2026-04-01-flat-session.md" not in names


def test_discover_notes_ignores_sharded_sessions(wiki_with_sessions):
    notes = discover_notes(wiki_with_sessions)
    names = {n.name for n in notes}
    assert "2026-04-02-sharded-session.md" not in names
