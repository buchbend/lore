"""Tests for `lore resume` CLI.

gh subprocess is mocked via monkeypatch on `lore_core.resume.gh_issues`
and `gh_prs` — those are now the canonical import sites since the
scope-aggregation logic was extracted from `lore_cli.resume_cmd` into
`lore_core.resume.gather()`.
"""

from __future__ import annotations

from textwrap import dedent

import pytest
from lore_cli import resume_cmd


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """Vault with one wiki containing a `_scopes.yml` + session notes."""
    vault_root = tmp_path / "vault"
    wiki = vault_root / "wiki" / "ccat"
    (wiki / "sessions").mkdir(parents=True)
    (wiki / "_scopes.yml").write_text(
        dedent(
            """\
            scopes:
              ccat:
                children:
                  data-center:
                    children:
                      data-transfer:
                        repo: ccatobs/data-transfer
                      system-integration:
                        repo: ccatobs/system-integration
            """
        )
    )
    # A matching v2 session note
    (wiki / "sessions" / "2026-04-10-retry-fix.md").write_text(
        dedent(
            """\
            ---
            schema_version: 2
            type: session
            created: 2026-04-10
            last_reviewed: 2026-04-10
            status: stable
            description: "retry fix session"
            scope: ccat:data-center:data-transfer
            repos: [ccatobs/data-transfer]
            ---
            # s
            """
        )
    )
    # A non-matching one (different scope)
    (wiki / "sessions" / "2026-04-09-unrelated.md").write_text(
        dedent(
            """\
            ---
            schema_version: 2
            type: session
            created: 2026-04-09
            last_reviewed: 2026-04-09
            status: stable
            description: "unrelated"
            scope: ccat:instrument:atm
            ---
            # s
            """
        )
    )
    monkeypatch.setenv("LORE_ROOT", str(vault_root))
    return vault_root, wiki


def _stub_gh(monkeypatch, issues=None, prs=None):
    issues = issues or {}
    prs = prs or {}

    def fake_gh_issues(repo, _flags):
        return issues.get(repo, [])

    def fake_gh_prs(repo, _flags):
        return prs.get(repo, [])

    # Patch the canonical import site (lore_core.resume) — the CLI
    # now delegates there.
    from lore_core import resume as core_resume

    monkeypatch.setattr(core_resume, "gh_issues", fake_gh_issues)
    monkeypatch.setattr(core_resume, "gh_prs", fake_gh_prs)


def test_resume_subtree_aggregates_issues(vault, monkeypatch, capsys):
    _stub_gh(
        monkeypatch,
        issues={
            "ccatobs/data-transfer": [{"number": 47, "title": "retry cap"}],
            "ccatobs/system-integration": [{"number": 10, "title": "trust CA"}],
        },
        prs={
            "ccatobs/data-transfer": [{"number": 31, "title": "wip", "isDraft": True}],
        },
    )
    rc = resume_cmd.main(["--scope", "ccat:data-center"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Subtree in `ccat`: 2 repo(s)" in out
    assert "#47 retry cap" in out
    assert "#10 trust CA" in out
    assert "#31 [draft] wip" in out
    # Session note matching by scope
    assert "2026-04-10" in out
    # Non-matching session excluded
    assert "2026-04-09" not in out


def test_resume_no_wiki_matches_emits_error(vault, monkeypatch, capsys):
    _stub_gh(monkeypatch)
    rc = resume_cmd.main(["--scope", "does-not-exist:path"])
    # gather() returns mode=scope with an `error` field; CLI still exits 0
    # because the operation completed successfully (empty result).
    assert rc == 0
    out = capsys.readouterr().out
    assert "No wiki claims scope" in out


def test_resume_exact_leaf(vault, monkeypatch, capsys):
    _stub_gh(
        monkeypatch,
        issues={"ccatobs/data-transfer": [{"number": 99, "title": "x"}]},
    )
    rc = resume_cmd.main(["--scope", "ccat:data-center:data-transfer"])
    assert rc == 0
    out = capsys.readouterr().out
    # Only the exact repo's issues appear
    assert "#99 x" in out
    assert "ccatobs/system-integration" not in out


def test_resume_json_output(vault, monkeypatch, capsys):
    _stub_gh(
        monkeypatch,
        issues={"ccatobs/data-transfer": [{"number": 47, "title": "retry cap"}]},
    )
    rc = resume_cmd.main(["--scope", "ccat:data-center", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    import json as _j

    envelope = _j.loads(out)
    assert envelope["schema"] == "lore.resume/1"
    data = envelope["data"]
    assert data["scope"] == "ccat:data-center"
    assert data["wiki"] == "ccat"
    assert len(data["members"]) == 2
    assert data["issues"]["ccatobs/data-transfer"] == [
        {"number": 47, "title": "retry cap"}
    ]


def test_resume_gh_silent_on_failure(vault, monkeypatch, capsys):
    """gh returning [] for every repo should still produce useful output."""
    _stub_gh(monkeypatch)  # both empty
    rc = resume_cmd.main(["--scope", "ccat:data-center"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "### Open issues" in out
    assert "_None matched._" in out
    assert "### Open PRs" in out


def test_resume_recent_no_args(vault, monkeypatch, capsys):
    """No-arg mode: recent sessions across all wikis."""
    _stub_gh(monkeypatch)
    rc = resume_cmd.main(["--days", "365"])  # widen to catch fixture sessions
    assert rc == 0
    out = capsys.readouterr().out
    assert "Resume: all wikis" in out
    assert "Recent sessions" in out
    # Both fixture sessions should appear
    assert "2026-04-10" in out
    assert "2026-04-09" in out


def test_resume_wiki_scoped_no_keyword(vault, monkeypatch, capsys):
    """--wiki without --keyword: recent in that wiki only."""
    _stub_gh(monkeypatch)
    rc = resume_cmd.main(["--wiki", "ccat", "--days", "365"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Resume: ccat" in out


@pytest.fixture
def sharded_vault(tmp_path, monkeypatch):
    """Vault using the canonical sharded layout: sessions/YYYY/MM/DD-slug.md.

    This is the layout `lore session new` actually writes (see
    `lore_core.session.scaffold` — `<wiki>/sessions/<YYYY>/<MM>/<DD>-<slug>.md`).
    The flat-layout fixture above predates sharding and only exercises the
    legacy form; without a sharded fixture the date-prefix bug in
    `_iter_session_notes` (parsed only the first 10 chars of the filename
    stem, which for sharded files is "DD-foo-bar") went unnoticed.
    """
    from datetime import date

    vault_root = tmp_path / "vault"
    wiki = vault_root / "wiki" / "private"
    today = date.today()
    shard = wiki / "sessions" / f"{today.year}" / f"{today.month:02d}"
    shard.mkdir(parents=True)
    (shard / f"{today.day:02d}-sharded-fix.md").write_text(
        dedent(
            f"""\
            ---
            schema_version: 2
            type: session
            created: {today.isoformat()}
            last_reviewed: {today.isoformat()}
            status: stable
            description: "sharded layout session"
            scope: private
            ---
            # s
            """
        )
    )
    monkeypatch.setenv("LORE_ROOT", str(vault_root))
    return vault_root, wiki


def test_resume_recent_finds_sharded_sessions(sharded_vault, capsys):
    """Recent-mode must walk the YYYY/MM/DD-slug.md sharded layout."""
    rc = resume_cmd.main([])  # default 3-day window covers today
    assert rc == 0
    out = capsys.readouterr().out
    assert "sharded-fix" in out, f"sharded session missing from output:\n{out}"
