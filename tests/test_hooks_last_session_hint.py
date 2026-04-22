"""Tests for _last_session_hint — recent session note breadcrumbs at SessionStart."""

from pathlib import Path

from lore_cli.hooks import _last_session_hint


def _write_session(wiki: Path, slug: str, description: str | None = None) -> None:
    sessions = wiki / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    lines.append("type: session")
    if description is not None:
        lines.append(f"description: '{description}'")
    lines.append("---")
    lines.append("")
    (sessions / f"{slug}.md").write_text("\n".join(lines))


def test_empty_sessions_dir(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki" / "private"
    wiki.mkdir(parents=True)
    assert _last_session_hint(wiki) == []


def test_no_sessions_dir(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki" / "private"
    wiki.mkdir(parents=True)
    assert _last_session_hint(wiki) == []


def test_returns_most_recent(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki" / "private"
    _write_session(wiki, "2026-04-20-old-session", "Old work")
    _write_session(wiki, "2026-04-21-middle-session", "Middle work")
    _write_session(wiki, "2026-04-22-latest-session", "Latest work")

    result = _last_session_hint(wiki, max_notes=2)
    assert len(result) == 2
    assert "[[2026-04-22-latest-session]]" in result[0]
    assert "Latest work" in result[0]
    assert "[[2026-04-21-middle-session]]" in result[1]
    assert "Middle work" in result[1]


def test_skips_notes_without_description(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki" / "private"
    _write_session(wiki, "2026-04-22-no-desc")
    _write_session(wiki, "2026-04-21-has-desc", "Has a description")

    result = _last_session_hint(wiki, max_notes=2)
    assert len(result) == 1
    assert "[[2026-04-21-has-desc]]" in result[0]


def test_wikilink_format(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki" / "private"
    _write_session(wiki, "2026-04-22-some-slug", "Some work")

    result = _last_session_hint(wiki, max_notes=1)
    assert len(result) == 1
    assert result[0].startswith("Last: [[2026-04-22-some-slug]]")
    assert "Some work" in result[0]


def test_single_note_available(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki" / "private"
    _write_session(wiki, "2026-04-22-only-one", "Only session")

    result = _last_session_hint(wiki, max_notes=2)
    assert len(result) == 1


def test_max_notes_respected(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki" / "private"
    for i in range(5):
        _write_session(wiki, f"2026-04-{20+i:02d}-session-{i}", f"Session {i}")

    result = _last_session_hint(wiki, max_notes=1)
    assert len(result) == 1
