"""Tests for _recent.md generation in the linter."""

from __future__ import annotations

from pathlib import Path

import pytest
from lore_core.lint import generate_recent_md, run_lint, SKIP_FILES


# ---------------------------------------------------------------------------
# Unit tests for generate_recent_md
# ---------------------------------------------------------------------------


@pytest.fixture
def wiki_with_sessions(tmp_path) -> Path:
    w = tmp_path / "mywiki"
    sessions = w / "sessions" / "2026" / "04"
    sessions.mkdir(parents=True)
    for day in range(1, 6):
        slug = f"{day:02d}-session-{day}.md"
        (sessions / slug).write_text(f"---\ntype: session\n---\n# Session {day}\n")
    return w


@pytest.fixture
def wiki_without_sessions(tmp_path) -> Path:
    w = tmp_path / "mywiki"
    (w / "concepts").mkdir(parents=True)
    return w


def test_recent_md_contains_wikilinks(wiki_with_sessions):
    content = generate_recent_md(wiki_with_sessions)
    assert content is not None
    assert "# Recent Sessions" in content
    assert "[[05-session-5]]" in content
    assert "[[01-session-1]]" in content


def test_recent_md_newest_first(wiki_with_sessions):
    content = generate_recent_md(wiki_with_sessions)
    assert content is not None
    lines = [l for l in content.splitlines() if l.startswith("- ")]
    assert lines[0] == "- [[05-session-5]]"
    assert lines[-1] == "- [[01-session-1]]"


def test_recent_md_none_without_sessions_dir(wiki_without_sessions):
    result = generate_recent_md(wiki_without_sessions)
    assert result is None


def test_recent_md_caps_at_max_entries(tmp_path):
    w = tmp_path / "wiki"
    sessions = w / "sessions" / "2026" / "04"
    sessions.mkdir(parents=True)
    for day in range(1, 31):
        slug = f"{day:02d}-session-{day}.md"
        (sessions / slug).write_text("---\ntype: session\n---\n")
    content = generate_recent_md(w, max_entries=20)
    assert content is not None
    wikilink_lines = [l for l in content.splitlines() if l.startswith("- ")]
    assert len(wikilink_lines) == 20
    # newest (day 30) should be first
    assert "[[30-session-30]]" in wikilink_lines[0]


def test_recent_md_excludes_skip_files(tmp_path):
    w = tmp_path / "wiki"
    sessions = w / "sessions" / "2026" / "04"
    sessions.mkdir(parents=True)
    (sessions / "01-real.md").write_text("---\ntype: session\n---\n")
    (sessions / "_recent.md").write_text("should be ignored")
    (sessions / "_index.md").write_text("should be ignored")
    content = generate_recent_md(w)
    assert content is not None
    assert "[[01-real]]" in content
    assert "_recent" not in content
    assert "_index" not in content


def test_recent_md_spans_months(tmp_path):
    w = tmp_path / "wiki"
    for month in ["03", "04"]:
        d = w / "sessions" / "2026" / month
        d.mkdir(parents=True)
    (w / "sessions" / "2026" / "03" / "28-march-session.md").write_text(
        "---\ntype: session\n---\n"
    )
    (w / "sessions" / "2026" / "04" / "02-april-session.md").write_text(
        "---\ntype: session\n---\n"
    )
    content = generate_recent_md(w)
    assert content is not None
    lines = [l for l in content.splitlines() if l.startswith("- ")]
    # April (2026/04) sorts after March (2026/03) → newest first
    assert "[[02-april-session]]" in lines[0]
    assert "[[28-march-session]]" in lines[1]


def test_recent_md_in_skip_files():
    assert "_recent.md" in SKIP_FILES


# ---------------------------------------------------------------------------
# Integration: run_lint writes sessions/_recent.md
# ---------------------------------------------------------------------------


def test_run_lint_creates_recent_md(tmp_path, monkeypatch):
    """run_lint writes sessions/_recent.md for wikis with a sessions/ dir."""
    wiki_root = tmp_path / "wiki"
    w = wiki_root / "mywiki"
    sessions = w / "sessions" / "2026" / "04"
    sessions.mkdir(parents=True)
    for day in [10, 11, 12]:
        slug = f"{day:02d}-test-{day}.md"
        (sessions / slug).write_text(
            f"---\ntype: session\ndescription: test {day}\n---\n# s{day}\n"
        )

    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    # get_wiki_root looks at LORE_ROOT/wiki
    monkeypatch.setattr("lore_core.lint.get_wiki_root", lambda: wiki_root)

    run_lint(json_output=True)

    recent = w / "sessions" / "_recent.md"
    assert recent.exists(), "sessions/_recent.md was not created"
    text = recent.read_text()
    assert "# Recent Sessions" in text
    assert "[[12-test-12]]" in text
    assert "[[10-test-10]]" in text


def test_run_lint_skips_recent_md_without_sessions(tmp_path, monkeypatch):
    """run_lint does NOT create sessions/_recent.md for wikis without sessions/."""
    wiki_root = tmp_path / "wiki"
    w = wiki_root / "mywiki"
    (w / "concepts").mkdir(parents=True)
    (w / "concepts" / "example.md").write_text(
        "---\ntype: concept\ndescription: ex\ntags: [a]\n---\n# Ex\n"
    )

    monkeypatch.setattr("lore_core.lint.get_wiki_root", lambda: wiki_root)

    run_lint(json_output=True)

    assert not (w / "sessions").exists()
    assert not (w / "sessions" / "_recent.md").exists()
