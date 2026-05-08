"""Tests for ``lore_core.stale_marker_writer`` — slice 5 of PRD #65."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from lore_core.schema import parse_frontmatter
from lore_core.stale_marker_writer import (
    StaleMarkerError,
    clear_stale,
    mark_stale,
)


def _make_note(path: Path, fm: str = "type: concept\n", body: str = "body\n") -> Path:
    path.write_text(f"---\n{fm}---\n{body}")
    return path


def test_mark_stale_writes_four_fields(tmp_path):
    p = _make_note(tmp_path / "n.md", fm="type: concept\ndescription: thing\n")
    mark_stale(p, reason="superseded by new approach", handle="alice", today=date(2026, 5, 8))
    fm = parse_frontmatter(p.read_text())
    assert fm["status"] == "stale"
    assert fm["stale_reason"] == "superseded by new approach"
    assert fm["stale_by"] == "alice"
    assert str(fm["stale_at"]) == "2026-05-08"


def test_mark_stale_preserves_existing_fields(tmp_path):
    p = _make_note(
        tmp_path / "n.md",
        fm="type: concept\ndescription: thing\ntags: [x]\nlast_reviewed: 2026-04-01\n",
    )
    mark_stale(p, reason="X", handle="alice", today=date(2026, 5, 8))
    fm = parse_frontmatter(p.read_text())
    assert fm["type"] == "concept"
    assert fm["description"] == "thing"
    assert fm["tags"] == ["x"]
    assert str(fm["last_reviewed"]) == "2026-04-01"


def test_mark_stale_preserves_body(tmp_path):
    p = _make_note(tmp_path / "n.md", body="# Title\n\nimportant body content\n")
    mark_stale(p, reason="X", handle="alice", today=date(2026, 5, 8))
    text = p.read_text()
    assert "# Title" in text
    assert "important body content" in text


def test_mark_stale_idempotent(tmp_path):
    p = _make_note(tmp_path / "n.md")
    mark_stale(p, reason="X", handle="alice", today=date(2026, 5, 8))
    first = p.read_text()
    mark_stale(p, reason="X", handle="alice", today=date(2026, 5, 8))
    second = p.read_text()
    assert first == second


def test_mark_stale_refuses_to_overwrite_existing_status(tmp_path):
    p = _make_note(tmp_path / "n.md", fm="type: concept\nstatus: active\n")
    with pytest.raises(StaleMarkerError):
        mark_stale(p, reason="X", handle="alice", today=date(2026, 5, 8))


def test_mark_stale_refuses_to_overwrite_existing_stale_reason(tmp_path):
    p = _make_note(
        tmp_path / "n.md",
        fm="type: concept\nstatus: stale\nstale_reason: prior reason\nstale_by: bob\nstale_at: 2026-04-01\n",
    )
    with pytest.raises(StaleMarkerError):
        mark_stale(p, reason="new reason", handle="alice", today=date(2026, 5, 8))


def test_mark_stale_requires_non_empty_reason(tmp_path):
    p = _make_note(tmp_path / "n.md")
    with pytest.raises(StaleMarkerError):
        mark_stale(p, reason="", handle="alice")
    with pytest.raises(StaleMarkerError):
        mark_stale(p, reason="   ", handle="alice")


def test_mark_stale_no_frontmatter_raises(tmp_path):
    p = tmp_path / "n.md"
    p.write_text("just body, no frontmatter\n")
    with pytest.raises(StaleMarkerError):
        mark_stale(p, reason="X", handle="alice")


def test_clear_stale_removes_only_four_fields(tmp_path):
    p = _make_note(
        tmp_path / "n.md",
        fm="type: concept\ndescription: thing\nstatus: stale\nstale_reason: X\nstale_by: alice\nstale_at: 2026-05-08\n",
        body="body\n",
    )
    clear_stale(p)
    fm = parse_frontmatter(p.read_text())
    assert fm["type"] == "concept"
    assert fm["description"] == "thing"
    assert "status" not in fm
    assert "stale_reason" not in fm
    assert "stale_by" not in fm
    assert "stale_at" not in fm
    assert "body" in p.read_text()


def test_clear_stale_no_op_when_no_markers(tmp_path):
    p = _make_note(tmp_path / "n.md", fm="type: concept\ndescription: thing\n")
    before = p.read_text()
    clear_stale(p)
    assert p.read_text() == before


def test_round_trip_mark_then_clear(tmp_path):
    """Reversibility: mark + clear returns to original confirmed state."""
    p = _make_note(tmp_path / "n.md", fm="type: concept\ndescription: thing\n")
    before = p.read_text()
    mark_stale(p, reason="test", handle="alice", today=date(2026, 5, 8))
    clear_stale(p)
    after = p.read_text()
    # FM key order may differ marginally but parse equivalence holds.
    fm_before = parse_frontmatter(before)
    fm_after = parse_frontmatter(after)
    assert fm_before == fm_after


def test_no_body_or_field_deletion_compliance(tmp_path):
    """Vault-wide edit policy: no body modifications, no field deletions
    outside the explicit clear_stale exit."""
    p = _make_note(
        tmp_path / "n.md",
        fm="type: concept\ndescription: keep me\ntags: [a, b]\n",
        body="# Title\n\nparagraph\n",
    )
    body_before = p.read_text().split("---\n", 2)[2]
    mark_stale(p, reason="X", handle="alice", today=date(2026, 5, 8))
    body_after = p.read_text().split("---\n", 2)[2]
    assert body_before == body_after
    fm = parse_frontmatter(p.read_text())
    assert fm["description"] == "keep me"
    assert fm["tags"] == ["a", "b"]
