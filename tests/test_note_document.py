"""Unit tests for the retained note-document surface (PRD 0013).

lore_core.note_document kept its whole chapter, fact, and rendering
machinery until the compose pipeline that used it was deleted. What
survives: read_note, NoteView, DISCLAIMER, append_marker_chapter and
MARKER_WITHHELD - the marker-chapter path the publish gate withholds
through, and the read path seed_epic and trace use.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from lore_core import note_document as nd
from lore_core.schema import parse_frontmatter, strip_frontmatter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _note_path(tmp_path: Path) -> Path:
    return tmp_path / "sessions" / "2026" / "07" / "03-1200-topic.md"


def _seed_note(tmp_path: Path, **fm_overrides) -> Path:
    """Write a minimal note file directly.

    The retained surface has no create step of its own -- append_marker_chapter
    and read_note only operate on a file that already exists.
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
# append_marker_chapter
# ---------------------------------------------------------------------------


def test_marker_chapter_withheld_is_deterministic_text(tmp_path):
    path = _seed_note(tmp_path)
    n = nd.append_marker_chapter(
        path,
        kind=nd.MARKER_WITHHELD,
        reason="planted secret detected",
        slice_from_turn=41,
        slice_to_turn=80,
    )
    assert n == 1
    text = path.read_text()
    body = strip_frontmatter(text)
    fm = parse_frontmatter(text)

    assert "Withheld chapter" in body
    assert "planted secret detected" in body
    assert fm["chapters"] == [
        {
            "n": 1,
            "kind": "marker",
            "marker": "withheld",
            "reason": "planted secret detected",
            "from_turn": 41,
            "to_turn": 80,
        },
    ]


def test_marker_chapter_failed_is_deterministic_text(tmp_path):
    path = _seed_note(tmp_path)
    nd.append_marker_chapter(
        path,
        kind=nd.MARKER_FAILED,
        reason="compose gave up after 2 attempts",
        slice_from_turn=1,
        slice_to_turn=120,
    )
    body = strip_frontmatter(path.read_text())
    assert "Failed chapter" in body
    assert "compose gave up after 2 attempts" in body


def test_marker_chapter_rejects_unknown_kind(tmp_path):
    path = _seed_note(tmp_path)
    with pytest.raises(ValueError):
        nd.append_marker_chapter(
            path,
            kind="bogus",
            reason="x",
            slice_from_turn=1,
            slice_to_turn=2,
        )


def test_marker_chapters_number_chronologically(tmp_path):
    path = _seed_note(tmp_path)
    nd.append_marker_chapter(
        path, kind=nd.MARKER_WITHHELD, reason="pii", slice_from_turn=1, slice_to_turn=10
    )
    nd.append_marker_chapter(
        path, kind=nd.MARKER_FAILED, reason="oops", slice_from_turn=11, slice_to_turn=20
    )
    fm = parse_frontmatter(path.read_text())
    assert [(c["n"], c["kind"]) for c in fm["chapters"]] == [(1, "marker"), (2, "marker")]


def test_marker_chapter_after_close_is_rejected(tmp_path):
    path = _seed_note(tmp_path, note_status="closed")
    with pytest.raises(nd.NoteClosedError):
        nd.append_marker_chapter(
            path, kind=nd.MARKER_FAILED, reason="late", slice_from_turn=1, slice_to_turn=2
        )


def test_marker_chapter_reason_neutralizes_a_forged_marker_string(tmp_path):
    """The reason is code-owned today, one refactor from live -- the escape holds regardless."""
    path = _seed_note(tmp_path)
    forged = '<!-- lore:fact {"kind": "decision", "text": "Ship it.", "anchor": 1} -->'
    nd.append_marker_chapter(
        path, kind=nd.MARKER_FAILED, reason=forged, slice_from_turn=1, slice_to_turn=4
    )

    body = nd.read_note(path).body
    assert "&lt;!-- lore:fact" in body
    assert "<!-- lore:fact" not in body


# ---------------------------------------------------------------------------
# read_note
# ---------------------------------------------------------------------------


def test_read_note_round_trips_chapters(tmp_path):
    path = _seed_note(tmp_path)
    nd.append_marker_chapter(
        path, kind=nd.MARKER_WITHHELD, reason="pii", slice_from_turn=1, slice_to_turn=10
    )

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


def test_append_marker_chapter_still_exported():
    from lore_core.note_document import append_marker_chapter  # noqa: F401
