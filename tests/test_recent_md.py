"""Tests for _recent.md generation in the linter."""

from __future__ import annotations

from pathlib import Path

import pytest
from lore_core.lint import (
    SKIP_FILES,
    generate_plan_recent_md,
    generate_recent_md,
    run_lint,
)
from lore_core.session_writer import session_path_sort_key


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


# ---------------------------------------------------------------------------
# session_path_sort_key — handles new DD-HHMM-slug shape and legacy DD-slug
# ---------------------------------------------------------------------------


def test_sort_key_new_shape_basic():
    p = Path("wiki/sessions/2026/04/28-1432-foo.md")
    assert session_path_sort_key(p) == (2026, 4, 28, 1432, "foo")


def test_sort_key_legacy_shape_collapses_hhmm_to_zero():
    """Legacy DD-slug.md has no time signal — treat as ``hhmm=0`` so it
    sorts at the *start* of its day (and thus at the *end* in reverse)."""
    p = Path("wiki/sessions/2026/04/28-foo.md")
    assert session_path_sort_key(p) == (2026, 4, 28, 0, "foo")


def test_sort_key_orders_intra_day_by_hhmm():
    morning = Path("wiki/sessions/2026/04/28-0900-bar.md")
    afternoon = Path("wiki/sessions/2026/04/28-1432-foo.md")
    legacy = Path("wiki/sessions/2026/04/28-old.md")
    files = [legacy, afternoon, morning]
    files.sort(key=session_path_sort_key, reverse=True)
    # newest → oldest: 14:32, 09:00, legacy(0)
    assert [f.name for f in files] == [
        "28-1432-foo.md",
        "28-0900-bar.md",
        "28-old.md",
    ]


def test_sort_key_unparseable_filename_sinks():
    """Files outside the expected shape should not crash and should
    sink to the bottom of any newest-first ranking."""
    bad = Path("wiki/sessions/2026/04/random-name.md")
    good = Path("wiki/sessions/2026/04/28-1432-foo.md")
    files = sorted([bad, good], key=session_path_sort_key, reverse=True)
    assert files[0] == good


def test_recent_md_intra_day_ordering(tmp_path):
    """generate_recent_md respects HHMM ordering within a single day."""
    w = tmp_path / "wiki"
    sessions = w / "sessions" / "2026" / "04"
    sessions.mkdir(parents=True)
    (sessions / "28-0900-morning.md").write_text("---\ntype: session\n---\n")
    (sessions / "28-1432-afternoon.md").write_text("---\ntype: session\n---\n")
    (sessions / "28-old.md").write_text("---\ntype: session\n---\n")
    content = generate_recent_md(w)
    assert content is not None
    lines = [l for l in content.splitlines() if l.startswith("- ")]
    assert lines[0] == "- [[28-1432-afternoon]]"
    assert lines[1] == "- [[28-0900-morning]]"
    assert lines[2] == "- [[28-old]]"


# ---------------------------------------------------------------------------
# generate_plan_recent_md — recency by last_reviewed / step_status_updated
# ---------------------------------------------------------------------------


def test_plan_recent_md_none_without_plans_dir(tmp_path):
    w = tmp_path / "wiki"
    (w / "concepts").mkdir(parents=True)
    assert generate_plan_recent_md(w) is None


def test_plan_recent_md_orders_by_last_reviewed(tmp_path):
    w = tmp_path / "wiki"
    plans = w / "plans"
    plans.mkdir(parents=True)
    (plans / "old.md").write_text(
        "---\ntype: plan\nstatus: active\n"
        "last_reviewed: '2026-04-20'\n---\n# old\n"
    )
    (plans / "new.md").write_text(
        "---\ntype: plan\nstatus: active\n"
        "last_reviewed: '2026-04-28'\n---\n# new\n"
    )
    content = generate_plan_recent_md(w)
    assert content is not None
    assert "# Recent Plans" in content
    lines = [l for l in content.splitlines() if l.startswith("- ")]
    assert lines[0] == "- [[new]] · active"
    assert lines[1] == "- [[old]] · active"


def test_plan_recent_md_step_status_updated_overrides_last_reviewed(tmp_path):
    """When step_status_updated is more recent than last_reviewed, it wins."""
    w = tmp_path / "wiki"
    plans = w / "plans"
    plans.mkdir(parents=True)
    (plans / "stale-review.md").write_text(
        "---\ntype: plan\nstatus: active\n"
        "last_reviewed: '2026-04-01'\n"
        "step_status_updated: '2026-04-28'\n---\n"
    )
    (plans / "recent-review.md").write_text(
        "---\ntype: plan\nstatus: active\n"
        "last_reviewed: '2026-04-15'\n---\n"
    )
    content = generate_plan_recent_md(w)
    assert content is not None
    lines = [l for l in content.splitlines() if l.startswith("- ")]
    assert lines[0] == "- [[stale-review]] · active"


def test_plan_recent_md_renders_status_badge(tmp_path):
    w = tmp_path / "wiki"
    plans = w / "plans"
    plans.mkdir(parents=True)
    (plans / "shipped.md").write_text(
        "---\ntype: plan\nstatus: done\nlast_reviewed: '2026-04-28'\n---\n"
    )
    (plans / "running.md").write_text(
        "---\ntype: plan\nstatus: active\nlast_reviewed: '2026-04-27'\n---\n"
    )
    content = generate_plan_recent_md(w)
    assert content is not None
    assert "- [[shipped]] · done" in content
    assert "- [[running]] · active" in content


def test_plan_recent_md_skips_lockfiles(tmp_path):
    w = tmp_path / "wiki"
    plans = w / "plans"
    plans.mkdir(parents=True)
    (plans / "real.md").write_text(
        "---\ntype: plan\nstatus: active\nlast_reviewed: '2026-04-28'\n---\n"
    )
    # Lockfile sentinels live alongside notes — must not be parsed.
    (plans / ".real.lock").write_text("")
    content = generate_plan_recent_md(w)
    assert content is not None
    assert "real" in content
    # Lockfiles aren't markdown so the glob already skips them, but
    # extra leading-dot guard pins the contract.
    assert ".real" not in content


def test_plan_recent_md_skips_non_plan_files(tmp_path):
    """A stray .md without ``type: plan`` is ignored."""
    w = tmp_path / "wiki"
    plans = w / "plans"
    plans.mkdir(parents=True)
    (plans / "real.md").write_text(
        "---\ntype: plan\nstatus: active\nlast_reviewed: '2026-04-28'\n---\n"
    )
    (plans / "stray.md").write_text("---\ntype: note\n---\nnot a plan\n")
    content = generate_plan_recent_md(w)
    assert content is not None
    assert "[[real]]" in content
    assert "[[stray]]" not in content


def test_run_lint_creates_plan_recent_md(tmp_path, monkeypatch):
    wiki_root = tmp_path / "wiki"
    w = wiki_root / "mywiki"
    plans = w / "plans"
    plans.mkdir(parents=True)
    (plans / "p1.md").write_text(
        "---\ntype: plan\nstatus: active\n"
        "description: first plan\n"
        "last_reviewed: '2026-04-28'\n---\n# P1\n"
    )

    monkeypatch.setattr("lore_core.lint.get_wiki_root", lambda: wiki_root)
    run_lint(json_output=True)

    recent = plans / "_recent.md"
    assert recent.exists()
    text = recent.read_text()
    assert "# Recent Plans" in text
    assert "[[p1]] · active" in text


def test_run_lint_skips_plan_recent_md_without_plans_dir(tmp_path, monkeypatch):
    wiki_root = tmp_path / "wiki"
    w = wiki_root / "mywiki"
    (w / "concepts").mkdir(parents=True)
    (w / "concepts" / "example.md").write_text(
        "---\ntype: concept\ndescription: ex\ntags: [a]\n---\n# Ex\n"
    )
    monkeypatch.setattr("lore_core.lint.get_wiki_root", lambda: wiki_root)
    run_lint(json_output=True)

    assert not (w / "plans").exists()
    assert not (w / "plans" / "_recent.md").exists()


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
