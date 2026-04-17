"""Tests for `lore resume --scope` CLI.

gh subprocess is mocked via monkeypatch on `lore_core.gh.run_gh`.
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

    monkeypatch.setattr(resume_cmd, "gh_issues", fake_gh_issues)
    monkeypatch.setattr(resume_cmd, "gh_prs", fake_gh_prs)


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
    rc = resume_cmd.run_resume("ccat:data-center")
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


def test_resume_no_wiki_matches_returns_2(vault, monkeypatch, capsys):
    _stub_gh(monkeypatch)
    rc = resume_cmd.run_resume("does-not-exist:path")
    assert rc == 2


def test_resume_exact_leaf(vault, monkeypatch, capsys):
    _stub_gh(
        monkeypatch,
        issues={"ccatobs/data-transfer": [{"number": 99, "title": "x"}]},
    )
    rc = resume_cmd.run_resume("ccat:data-center:data-transfer")
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
    rc = resume_cmd.run_resume("ccat:data-center", json_output=True)
    assert rc == 0
    out = capsys.readouterr().out
    import json as _j

    data = _j.loads(out)
    assert data["scope"] == "ccat:data-center"
    assert data["wiki"] == "ccat"
    assert len(data["members"]) == 2
    assert data["issues"]["ccatobs/data-transfer"] == [
        {"number": 47, "title": "retry cap"}
    ]


def test_resume_gh_silent_on_failure(vault, monkeypatch, capsys):
    """gh returning [] for every repo should still produce useful output."""
    _stub_gh(monkeypatch)  # both empty
    rc = resume_cmd.run_resume("ccat:data-center")
    assert rc == 0
    out = capsys.readouterr().out
    assert "### Open issues" in out
    assert "_None matched._" in out
    assert "### Open PRs" in out
