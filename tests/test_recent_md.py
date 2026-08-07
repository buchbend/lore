"""Session-note index generation retired from the linter (PRD 0013).

`generate_recent_txt` and the note scan's `sessions/` walk are gone —
nothing writes a session note since the compose pipeline was retired, so
there is nothing left to index.
"""

from __future__ import annotations

from lore_core.lint import run_lint


def test_generate_recent_txt_is_gone() -> None:
    import lore_core.lint as lint

    assert not hasattr(lint, "generate_recent_txt")


def test_run_lint_writes_no_recent_txt_for_a_wiki_with_sessions(tmp_path, monkeypatch):
    """AC: `lore lint` writes no `_recent.txt` for a wiki holding `sessions/`."""
    wiki_root = tmp_path / "wiki"
    w = wiki_root / "mywiki"
    sessions = w / "sessions" / "2026" / "04"
    sessions.mkdir(parents=True)
    for day in [10, 11, 12]:
        slug = f"{day:02d}-test-{day}.md"
        text = f"---\ntype: session\ndescription: test {day}\n---\n# s{day}\n"
        (sessions / slug).write_text(text)

    monkeypatch.setattr("lore_core.lint.get_wiki_root", lambda: wiki_root)

    run_lint(json_output=True)

    assert not (w / "sessions" / "_recent.txt").exists()


def test_run_lint_reports_no_session_count(tmp_path, monkeypatch):
    """AC: the report states no session-note count for a wiki holding `sessions/`."""
    wiki_root = tmp_path / "wiki"
    w = wiki_root / "mywiki"
    sessions = w / "sessions" / "2026" / "04"
    sessions.mkdir(parents=True)
    (sessions / "10-test.md").write_text("---\ntype: session\ndescription: test\n---\n# s\n")
    monkeypatch.setattr("lore_core.lint.get_wiki_root", lambda: wiki_root)

    run_lint(json_output=True)

    index_text = (w / "_index.txt").read_text()
    assert "session" not in index_text.lower()
