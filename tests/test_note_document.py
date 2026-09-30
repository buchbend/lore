"""Unit tests for the retained note-document surface (PRD 0013).

lore_core.note_document kept its whole chapter, fact, and rendering
machinery until the compose pipeline that used it was deleted. What
survives: read_note, NoteView and DISCLAIMER - the read path seed_epic and
trace use. The marker-chapter writer left with the publish gate (issue 423).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from lore_core import note_document as nd

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _note_path(tmp_path: Path) -> Path:
    return tmp_path / "sessions" / "2026" / "07" / "03-1200-topic.md"


def _seed_note(tmp_path: Path, **fm_overrides) -> Path:
    """Write a minimal note file directly.

    The retained surface has no create step of its own -- read_note only
    operates on a file that already exists.
    """
    path = fm_overrides.pop("path", _note_path(tmp_path))
    fm = {
        "schema_version": 2,
        "type": "session",
        "note_status": "open",
        "created": "2026-07-03",
        "last_reviewed": "2026-07-03",
        "title": "Working on the buffer flush path",
        "description": "deterministic session note",
        "scope": "lore",
        "chapters": [],
    }
    fm.update(fm_overrides)
    path.parent.mkdir(parents=True, exist_ok=True)
    dumped = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    path.write_text(f"---\n{dumped}\n---\n\n{nd.DISCLAIMER}\n")
    return path


# ---------------------------------------------------------------------------
# read_note
# ---------------------------------------------------------------------------


def test_read_note_round_trips_chapters(tmp_path):
    marker = {"n": 1, "kind": "marker", "marker": "withheld", "from_turn": 1, "to_turn": 10}
    path = _seed_note(tmp_path, chapters=[marker])

    view = nd.read_note(path)
    assert view.closed is False
    assert [c["kind"] for c in view.chapters] == ["marker"]
    assert view.frontmatter["title"] == "Working on the buffer flush path"
    assert nd.DISCLAIMER in view.body


def test_read_note_reports_a_closed_note(tmp_path):
    path = _seed_note(tmp_path, note_status="closed")
    assert nd.read_note(path).closed is True


# ---------------------------------------------------------------------------
# no-LLM structural guarantee
# ---------------------------------------------------------------------------


def test_module_has_no_llm_wiring():
    """The document core must never touch an LLM or adapter."""
    src = Path(nd.__file__).read_text()
    for forbidden in ("lore_adapters", "llm_client", "get_adapter", "compose_session"):
        assert forbidden not in src, f"note_document must not reference {forbidden!r}"


# ---------------------------------------------------------------------------
# Retained-surface guard (PRD 0013)
# ---------------------------------------------------------------------------


def test_create_note_is_gone():
    with pytest.raises(ImportError):
        from lore_core.note_document import create_note  # noqa: F401


def test_append_chapter_is_gone():
    with pytest.raises(ImportError):
        from lore_core.note_document import append_chapter  # noqa: F401


def test_render_note_is_gone():
    with pytest.raises(ImportError):
        from lore_core.note_document import render_note  # noqa: F401


def test_read_note_still_exported():
    from lore_core.note_document import read_note  # noqa: F401


def test_append_marker_chapter_is_gone():
    with pytest.raises(ImportError):
        from lore_core.note_document import append_marker_chapter  # noqa: F401
