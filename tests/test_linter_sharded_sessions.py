"""Linter finds sessions under both flat and sharded layouts."""

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
    (w / "sessions" / "alice").mkdir()
    (w / "sessions" / "alice" / "2026-04-03-alice-session.md").write_text("z")
    return w


def test_discover_notes_finds_flat_and_sharded(wiki_with_sessions):
    notes = discover_notes(wiki_with_sessions)
    names = {n.name for n in notes}
    assert "2026-04-01-flat-session.md" in names
    assert "2026-04-02-sharded-session.md" in names
    assert "2026-04-03-alice-session.md" in names


def test_discover_notes_preserves_path_structure(wiki_with_sessions):
    notes = discover_notes(wiki_with_sessions)
    by_name = {n.name: n for n in notes}
    sharded = by_name["2026-04-02-sharded-session.md"]
    assert "buchbend" in sharded.parts
