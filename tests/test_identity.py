"""Tests for lore_core.identity — user aliasing + team-mode detection."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
from lore_core import identity


@pytest.fixture
def solo_wiki(tmp_path) -> Path:
    w = tmp_path / "wiki"
    w.mkdir()
    return w


@pytest.fixture
def team_wiki(tmp_path) -> Path:
    w = tmp_path / "wiki"
    w.mkdir()
    (w / "_users.yml").write_text(
        dedent(
            """\
            users:
              - handle: buchbend
                display_name: Christof
                aliases:
                  emails:
                    - christof@example.com
                    - buchbend@mail.de
              - handle: alice
                display_name: Alice
                aliases:
                  emails:
                    - alice@example.com
            """
        )
    )
    return w


# ---------- resolve_handle ----------


def test_resolve_handle_solo_fallback(solo_wiki):
    assert identity.resolve_handle(solo_wiki, "foo@bar.com") == "foo"


def test_resolve_handle_team_match(team_wiki):
    assert identity.resolve_handle(team_wiki, "christof@example.com") == "buchbend"
    assert identity.resolve_handle(team_wiki, "buchbend@mail.de") == "buchbend"
    assert identity.resolve_handle(team_wiki, "alice@example.com") == "alice"


def test_resolve_handle_team_unknown_falls_back_to_local_part(team_wiki):
    assert identity.resolve_handle(team_wiki, "stranger@elsewhere.com") == "stranger"


def test_resolve_handle_empty_email(team_wiki):
    assert identity.resolve_handle(team_wiki, "") == ""


# ---------- team_mode_active ----------


def test_team_mode_active_solo(solo_wiki):
    assert identity.team_mode_active(solo_wiki) is False


def test_team_mode_active_team(team_wiki):
    assert identity.team_mode_active(team_wiki) is True


# ---------- aliased / unaliased ----------


def test_aliased_emails_collects_all(team_wiki):
    aliased = identity.aliased_emails(team_wiki)
    assert aliased == {
        "christof@example.com",
        "buchbend@mail.de",
        "alice@example.com",
    }


def test_aliased_emails_empty_for_solo(solo_wiki):
    assert identity.aliased_emails(solo_wiki) == set()


# ---------- git author discovery (mocked) ----------


def test_distinct_git_authors_mocked(solo_wiki, monkeypatch):
    import subprocess

    class FakeResult:
        returncode = 0
        stdout = "alice@example.com\nbob@example.com\nalice@example.com\n"

    def fake_run(*a, **kw):
        return FakeResult()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert identity.distinct_git_authors(solo_wiki) == {
        "alice@example.com",
        "bob@example.com",
    }


def test_distinct_git_authors_no_git_returns_empty(solo_wiki, monkeypatch):
    import subprocess

    def fake_run(*a, **kw):
        raise OSError("gh? git? not here")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert identity.distinct_git_authors(solo_wiki) == set()


# ---------- team_mode_recommended ----------


def test_team_mode_recommended_solo_with_two_authors(solo_wiki, monkeypatch):
    monkeypatch.setattr(
        identity,
        "distinct_git_authors",
        lambda _w: {"alice@example.com", "bob@example.com"},
    )
    assert identity.team_mode_recommended(solo_wiki) is True


def test_team_mode_recommended_solo_with_one_author(solo_wiki, monkeypatch):
    monkeypatch.setattr(identity, "distinct_git_authors", lambda _w: {"alice@example.com"})
    assert identity.team_mode_recommended(solo_wiki) is False


def test_team_mode_recommended_false_when_already_team(team_wiki, monkeypatch):
    """Even with many authors, already-team wikis don't get the hint."""
    monkeypatch.setattr(
        identity,
        "distinct_git_authors",
        lambda _w: {"a@x", "b@x", "c@x"},
    )
    assert identity.team_mode_recommended(team_wiki) is False


# ---------- resolve_display_name ----------


@pytest.fixture
def rooted_wiki(tmp_path) -> Path:
    """A wiki at the real `<lore_root>/wiki/<name>` shape, solo mode."""
    w = tmp_path / "wiki" / "myvault"
    w.mkdir(parents=True)
    return w


def test_resolve_display_name_falls_back_to_root_config(rooted_wiki):
    from lore_core.root_config import set_field

    lore_root = rooted_wiki.parent.parent
    set_field(lore_root, "user.display_name", "Christof")
    assert identity.resolve_display_name(rooted_wiki, "buchbend") == "Christof"


def test_resolve_display_name_team_match_wins_over_root_config(rooted_wiki):
    from lore_core.root_config import set_field

    lore_root = rooted_wiki.parent.parent
    set_field(lore_root, "user.display_name", "Wrong Name")
    (rooted_wiki / "_users.yml").write_text(
        dedent(
            """\
            users:
              - handle: buchbend
                display_name: Christof
            """
        )
    )
    assert identity.resolve_display_name(rooted_wiki, "buchbend") == "Christof"


def test_resolve_display_name_no_fallback_when_root_config_unset(rooted_wiki):
    assert identity.resolve_display_name(rooted_wiki, "buchbend") == ""


def test_resolve_display_name_empty_handle_still_empty(rooted_wiki):
    from lore_core.root_config import set_field

    set_field(rooted_wiki.parent.parent, "user.display_name", "Christof")
    assert identity.resolve_display_name(rooted_wiki, "") == ""


def test_session_note_dir_is_gone():
    with pytest.raises(ImportError):
        from lore_core.identity import session_note_dir  # noqa: F401
