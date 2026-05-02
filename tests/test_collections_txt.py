"""Tests for `_concepts.txt` and `_decisions.txt` collection generation.

These flat per-type wikilink collections are written by `lore lint` at
the wiki root. The `.txt` extension keeps them out of the wikilink
graph (`wikilinks.py` globs `.md` only), so the collection itself
never becomes a god-node.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lore_core.lint import (
    SKIP_FILES,
    NoteInfo,
    generate_type_collection_txt,
    run_lint,
)


# ---------------------------------------------------------------------------
# Unit tests for generate_type_collection_txt
# ---------------------------------------------------------------------------


def _note(filename: str, note_type: str, *, description: str = "", lifecycle: str = "active",
          superseded_by=None, tags=None) -> NoteInfo:
    return NoteInfo(
        path=f"{note_type}s/{filename}.md",
        filename=filename,
        wiki="mywiki",
        note_type=note_type,
        status=None,
        lifecycle=lifecycle,
        superseded_by=superseded_by,
        description=description or None,
        tags=tags or [],
        created="2026-04-01",
        last_reviewed="2026-04-01",
        lines=10,
        links_out=[],
    )


def test_generate_concepts_txt_returns_none_when_no_concepts():
    notes = [_note("foo", "decision")]
    result = generate_type_collection_txt(
        "mywiki", notes, note_type="concept", title="Concepts",
    )
    assert result is None


def test_generate_concepts_txt_lists_only_concepts():
    notes = [
        _note("alpha", "concept", description="alpha concept"),
        _note("bravo", "decision", description="bravo decision"),
        _note("charlie", "concept", description="charlie concept"),
    ]
    result = generate_type_collection_txt(
        "mywiki", notes, note_type="concept", title="Concepts",
    )
    assert result is not None
    assert "[[alpha]]" in result
    assert "[[charlie]]" in result
    # Decisions must not appear in the concepts collection.
    assert "[[bravo]]" not in result


def test_generate_concepts_txt_sorts_by_filename():
    notes = [
        _note("zulu", "concept"),
        _note("alpha", "concept"),
        _note("mike", "concept"),
    ]
    result = generate_type_collection_txt(
        "mywiki", notes, note_type="concept", title="Concepts",
    )
    assert result is not None
    lines = [l for l in result.splitlines() if l.startswith("- ")]
    assert lines[0].startswith("- [[alpha]]")
    assert lines[1].startswith("- [[mike]]")
    assert lines[2].startswith("- [[zulu]]")


def test_generate_decisions_txt_marks_drafts():
    notes = [
        _note("ready", "decision", lifecycle="active"),
        _note("wip", "decision", lifecycle="draft"),
    ]
    result = generate_type_collection_txt(
        "mywiki", notes, note_type="decision", title="Decisions",
    )
    assert result is not None
    # The DRAFT badge appears next to the draft, not the ready note.
    draft_line = [l for l in result.splitlines() if "[[wip]]" in l][0]
    ready_line = [l for l in result.splitlines() if "[[ready]]" in l][0]
    assert "DRAFT" in draft_line
    assert "DRAFT" not in ready_line


def test_generate_concepts_txt_marks_superseded():
    notes = [
        _note("old", "concept", lifecycle="superseded", superseded_by="new"),
    ]
    result = generate_type_collection_txt(
        "mywiki", notes, note_type="concept", title="Concepts",
    )
    assert result is not None
    line = [l for l in result.splitlines() if "[[old]]" in l][0]
    assert "SUPERSEDED" in line
    assert "[[new]]" in line


def test_generate_concepts_txt_includes_descriptions():
    notes = [_note("alpha", "concept", description="The alpha concept")]
    result = generate_type_collection_txt(
        "mywiki", notes, note_type="concept", title="Concepts",
    )
    assert result is not None
    assert "The alpha concept" in result


def test_generate_concepts_txt_includes_wiki_name_in_header():
    notes = [_note("alpha", "concept")]
    result = generate_type_collection_txt(
        "ccat", notes, note_type="concept", title="Concepts",
    )
    assert result is not None
    assert "CCAT" in result.splitlines()[0]
    assert "Concepts" in result.splitlines()[0]


# ---------------------------------------------------------------------------
# Integration: run_lint emits collection .txt files at wiki root
# ---------------------------------------------------------------------------


def test_run_lint_creates_concepts_txt(tmp_path, monkeypatch):
    wiki_root = tmp_path / "wiki"
    w = wiki_root / "mywiki"
    (w / "concepts").mkdir(parents=True)
    (w / "concepts" / "alpha.md").write_text(
        "---\ntype: concept\ndescription: alpha concept\n"
        "created: '2026-04-01'\nlast_reviewed: '2026-04-01'\n"
        "tags: [topic/x]\n---\n# Alpha\n"
    )
    (w / "concepts" / "bravo.md").write_text(
        "---\ntype: concept\ndescription: bravo concept\n"
        "created: '2026-04-01'\nlast_reviewed: '2026-04-01'\n"
        "tags: [topic/y]\n---\n# Bravo\n"
    )

    monkeypatch.setattr("lore_core.lint.get_wiki_root", lambda: wiki_root)
    run_lint(json_output=True)

    collection = w / "_concepts.txt"
    assert collection.exists(), "wiki root _concepts.txt was not created"
    text = collection.read_text()
    assert "[[alpha]]" in text
    assert "[[bravo]]" in text


def test_run_lint_creates_decisions_txt(tmp_path, monkeypatch):
    wiki_root = tmp_path / "wiki"
    w = wiki_root / "mywiki"
    (w / "decisions").mkdir(parents=True)
    (w / "decisions" / "use-postgres.md").write_text(
        "---\ntype: decision\ndescription: use Postgres\n"
        "created: '2026-04-01'\nlast_reviewed: '2026-04-01'\n"
        "tags: [topic/db]\n---\n# Use Postgres\n"
    )

    monkeypatch.setattr("lore_core.lint.get_wiki_root", lambda: wiki_root)
    run_lint(json_output=True)

    collection = w / "_decisions.txt"
    assert collection.exists(), "wiki root _decisions.txt was not created"
    text = collection.read_text()
    assert "[[use-postgres]]" in text


def test_run_lint_skips_collections_when_no_notes_of_type(tmp_path, monkeypatch):
    """No concepts in the wiki → no _concepts.txt written."""
    wiki_root = tmp_path / "wiki"
    w = wiki_root / "mywiki"
    (w / "decisions").mkdir(parents=True)
    (w / "decisions" / "single.md").write_text(
        "---\ntype: decision\ndescription: only decision\n"
        "created: '2026-04-01'\nlast_reviewed: '2026-04-01'\n"
        "tags: [topic/x]\n---\n"
    )

    monkeypatch.setattr("lore_core.lint.get_wiki_root", lambda: wiki_root)
    run_lint(json_output=True)

    assert (w / "_decisions.txt").exists()
    assert not (w / "_concepts.txt").exists()


def test_collection_txts_are_in_skip_files():
    """_concepts.txt and _decisions.txt must be skip-listed so they're
    treated as derived/regenerable, not link targets."""
    assert "_concepts.txt" in SKIP_FILES
    assert "_decisions.txt" in SKIP_FILES
    assert "_threads.txt" in SKIP_FILES


def test_run_lint_removes_legacy_threads_md(tmp_path, monkeypatch):
    """A wiki carrying a legacy ``threads.md`` from a pre-upgrade run
    has it removed by lint so it doesn't drift in parallel with the
    new ``_threads.txt``."""
    wiki_root = tmp_path / "wiki"
    w = wiki_root / "mywiki"
    (w / "concepts").mkdir(parents=True)
    (w / "concepts" / "alpha.md").write_text(
        "---\ntype: concept\ndescription: a\ntags: [t]\n---\n"
    )
    legacy = w / "threads.md"
    legacy.write_text("# old threads.md content\n")

    monkeypatch.setattr("lore_core.lint.get_wiki_root", lambda: wiki_root)
    run_lint(json_output=True)

    assert not legacy.exists(), "lint must clean up legacy threads.md"


def test_run_lint_removes_legacy_recent_md(tmp_path, monkeypatch):
    """Legacy ``sessions/_recent.md`` and ``plans/_recent.md`` cleaned
    up so they don't drift alongside the new ``.txt`` versions."""
    wiki_root = tmp_path / "wiki"
    w = wiki_root / "mywiki"
    sessions = w / "sessions" / "2026" / "04"
    sessions.mkdir(parents=True)
    (sessions / "01-session-1.md").write_text(
        "---\ntype: session\n---\n"
    )
    plans = w / "plans"
    plans.mkdir()
    (plans / "p1.md").write_text(
        "---\ntype: plan\nstatus: active\nlast_reviewed: '2026-04-28'\n---\n"
    )
    legacy_session_recent = w / "sessions" / "_recent.md"
    legacy_session_recent.write_text("legacy")
    legacy_plan_recent = w / "plans" / "_recent.md"
    legacy_plan_recent.write_text("legacy")

    monkeypatch.setattr("lore_core.lint.get_wiki_root", lambda: wiki_root)
    run_lint(json_output=True)

    assert not legacy_session_recent.exists()
    assert not legacy_plan_recent.exists()
    # New .txt versions exist.
    assert (w / "sessions" / "_recent.txt").exists()
    assert (w / "plans" / "_recent.txt").exists()
