"""Tests for _last_session_hint — recent session note breadcrumbs at SessionStart."""

from pathlib import Path

from lore_cli.hooks import _last_session_hint


def _write_session(
    wiki: Path, slug: str, description: str | None = None, summary: str | None = None,
) -> None:
    sessions = wiki / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    lines.append("type: session")
    if description is not None:
        lines.append(f"description: '{description}'")
    if summary is not None:
        lines.append(f"summary: '{summary}'")
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
    assert result[0] == ("2026-04-22-latest-session", "Latest work")
    assert result[1] == ("2026-04-21-middle-session", "Middle work")


def test_skips_notes_without_description(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki" / "private"
    _write_session(wiki, "2026-04-22-no-desc")
    _write_session(wiki, "2026-04-21-has-desc", "Has a description")

    result = _last_session_hint(wiki, max_notes=2)
    assert len(result) == 1
    assert result[0] == ("2026-04-21-has-desc", "Has a description")


def test_tuple_format(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki" / "private"
    _write_session(wiki, "2026-04-22-some-slug", "Some work")

    result = _last_session_hint(wiki, max_notes=1)
    assert len(result) == 1
    slug, desc = result[0]
    assert slug == "2026-04-22-some-slug"
    assert desc == "Some work"


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


def test_summary_preferred_over_description(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki" / "private"
    _write_session(
        wiki, "2026-04-22-both-fields",
        description="Short title",
        summary="Detailed summary of what was decided and changed.",
    )

    result = _last_session_hint(wiki, max_notes=1)
    assert len(result) == 1
    _, desc = result[0]
    assert "Detailed summary" in desc
    assert "Short title" not in desc


def test_falls_back_to_description_when_no_summary(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki" / "private"
    _write_session(wiki, "2026-04-22-desc-only", description="Just a description")

    result = _last_session_hint(wiki, max_notes=1)
    assert len(result) == 1
    _, desc = result[0]
    assert desc == "Just a description"
