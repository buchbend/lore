"""Tests for the schema-v2 SessionStart path.

Covers the pure helpers (scope-tree walk, filter parsing, walk-up of
`CLAUDE.md`) and the orchestrator. SessionStart itself never calls
`gh` — issue/PR counts were dropped from the banner — so `gh.split_filter`
and the formatters are exercised directly against `lore_core.gh`.
"""

from __future__ import annotations

import pytest
from lore_cli import hooks
from lore_core import session_start

SCOPES_YML_SAMPLE = {
    "scopes": {
        "ccat": {
            "label": "CCAT",
            "children": {
                "data-center": {
                    "label": "Data center",
                    "children": {
                        "data-transfer": {"repo": "ccatobs/data-transfer"},
                        "system-integration": {"repo": "ccatobs/system-integration"},
                        "production-services": {"repo": "ccatobs/production-services"},
                    },
                },
                "instrument": {
                    "children": {
                        "atm-calibration": {"repo": "ccatobs/atm-calibration"},
                    },
                },
            },
        },
    }
}


# ---------- gh.split_filter (was hooks._split_filter pre-0.12.0) ----------


@pytest.mark.parametrize("raw,expected", [
    ("", []),
    ("--assignee @me", ["--assignee", "@me"]),
    ("--assignee @me --state open", ["--assignee", "@me", "--state", "open"]),
    ('--label "needs triage"', ["--label", "needs triage"]),
    (None, []),
])
def test_split_filter(raw, expected):
    from lore_core import gh
    assert gh.split_filter(raw) == expected


def test_split_filter_malformed_falls_back_to_whitespace():
    # Unterminated quote — shlex raises, fallback kicks in
    from lore_core import gh
    assert gh.split_filter('--label "oops') == ["--label", '"oops']


# ---------- scope tree walk ----------


def test_walk_scope_leaves():
    scopes = SCOPES_YML_SAMPLE["scopes"]
    leaves = sorted(hooks._walk_scope_leaves(scopes))
    assert leaves == sorted([
        ("ccat:data-center:data-transfer", "ccatobs/data-transfer"),
        ("ccat:data-center:system-integration", "ccatobs/system-integration"),
        ("ccat:data-center:production-services", "ccatobs/production-services"),
        ("ccat:instrument:atm-calibration", "ccatobs/atm-calibration"),
    ])


def test_subtree_siblings_returns_same_parent():
    sibs = hooks._subtree_siblings(
        SCOPES_YML_SAMPLE,
        "ccat:data-center:data-transfer",
    )
    repos = {r for _, r in sibs}
    assert repos == {"ccatobs/system-integration", "ccatobs/production-services"}


def test_subtree_siblings_excludes_current_scope():
    sibs = hooks._subtree_siblings(
        SCOPES_YML_SAMPLE,
        "ccat:data-center:data-transfer",
    )
    assert all(scope != "ccat:data-center:data-transfer" for scope, _ in sibs)


def test_subtree_siblings_top_level_has_no_siblings():
    # `ccat` has no parent → no subtree
    assert hooks._subtree_siblings(SCOPES_YML_SAMPLE, "ccat") == []


def test_subtree_siblings_unknown_scope_returns_empty():
    # The scope itself isn't in the tree, but its prefix is
    sibs = hooks._subtree_siblings(
        SCOPES_YML_SAMPLE,
        "ccat:data-center:new-repo",
    )
    repos = {r for _, r in sibs}
    # All real siblings under ccat:data-center come back
    assert repos == {
        "ccatobs/data-transfer",
        "ccatobs/system-integration",
        "ccatobs/production-services",
    }


def test_subtree_siblings_empty_yml():
    assert hooks._subtree_siblings({}, "anything") == []


# ---------- _load_scopes_yml ----------


def test_load_scopes_yml_missing(tmp_path):
    assert hooks._load_scopes_yml(tmp_path) == {}


def test_load_scopes_yml_malformed(tmp_path):
    (tmp_path / "_scopes.yml").write_text("::: not yaml :::")
    assert hooks._load_scopes_yml(tmp_path) == {}


# ---------- _resolve_attach_block ----------


def test_resolve_attach_block_returns_scope_and_merged_block(tmp_path, monkeypatch):
    """_resolve_attach_block is a thin wrapper over the registry resolver.
    It returns the synthetic claude_md_path sentinel and a block dict
    derived from the attachment."""
    from datetime import UTC, datetime

    from lore_core.session import _resolve_attach_block
    from lore_core.state.attachments import Attachment, AttachmentsFile

    parent = tmp_path / "repo"
    child = parent / "sub" / "deep"
    child.mkdir(parents=True)

    (tmp_path / ".lore").mkdir()
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    af = AttachmentsFile(tmp_path); af.load()
    af.add(Attachment(
        path=parent, wiki="ccat", scope="ccat:data-center:data-transfer",
        attached_at=datetime(2026, 4, 22, 9, 0, tzinfo=UTC), source="manual",
    ))
    af.save()

    result = _resolve_attach_block(child)
    assert result is not None
    path, block = result
    assert path == parent / "CLAUDE.md"      # synthetic sentinel
    assert block["wiki"] == "ccat"
    assert block["scope"] == "ccat:data-center:data-transfer"


def test_resolve_attach_block_returns_none_when_absent(tmp_path, monkeypatch):
    from lore_core.session import _resolve_attach_block
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    (tmp_path / ".lore").mkdir()
    assert _resolve_attach_block(tmp_path) is None


def test_resolve_attach_block_missing_file(tmp_path, monkeypatch):
    from lore_core.session import _resolve_attach_block
    monkeypatch.delenv("LORE_ROOT", raising=False)
    assert _resolve_attach_block(tmp_path) is None


# ---------- formatters ----------


def test_format_issue_line():
    from lore_core import gh
    assert gh.format_issue_line({"number": 47, "title": "retry cap"}) == "- #47 retry cap"


def test_format_pr_line_draft():
    from lore_core import gh
    assert gh.format_pr_line(
        {"number": 31, "title": "atm-table v2", "isDraft": True}
    ) == "- #31 [draft] atm-table v2"


def test_format_pr_line_ready():
    from lore_core import gh
    assert gh.format_pr_line(
        {"number": 32, "title": "bugfix", "isDraft": False}
    ) == "- #32 bugfix"


# ---------- orchestrator (mocked gh) ----------


@pytest.fixture
def fake_vault(tmp_path, monkeypatch):
    """LORE_ROOT → tmp vault with one wiki + `_scopes.yml`."""
    vault = tmp_path / "vault"
    wiki = vault / "wiki" / "ccat"
    (wiki / "sessions").mkdir(parents=True)
    (wiki / "_catalog.json").write_text(
        '{"stats": {"total_notes": 14}, "sections": {}}'
    )
    (wiki / "_scopes.yml").write_text(
        """scopes:
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
    monkeypatch.setenv("LORE_ROOT", str(vault))
    return vault, wiki


def _register_attachment(lore_root: Path, repo: Path, *, wiki: str, scope: str) -> None:
    """Register ``repo`` in ``lore_root/.lore/attachments.json``."""
    from datetime import UTC, datetime
    from lore_core.state.attachments import Attachment, AttachmentsFile

    (lore_root / ".lore").mkdir(exist_ok=True)
    af = AttachmentsFile(lore_root); af.load()
    af.add(Attachment(
        path=repo, wiki=wiki, scope=scope,
        attached_at=datetime(2026, 4, 22, 9, 0, tzinfo=UTC), source="manual",
    ))
    af.save()


def test_session_start_from_lore_happy_path(fake_vault, tmp_path, monkeypatch):
    vault, wiki = fake_vault
    repo_dir = tmp_path / "data-transfer"
    repo_dir.mkdir()
    _register_attachment(vault, repo_dir, wiki="ccat", scope="ccat:data-center:data-transfer")
    (repo_dir / ".lore.yml").write_text(
        "wiki: ccat\nscope: ccat:data-center:data-transfer\nbackend: github\n"
    )
    monkeypatch.setattr(session_start, "current_repo", lambda _cwd: "ccatobs/data-transfer")

    out = hooks._session_start(str(repo_dir))
    assert ": active" in out

    # Status line first, directive postscript last.
    status_pos = out.find(": active")
    directives_pos = out.find("## Directive")
    assert status_pos < directives_pos, (
        "directive should be a postscript, after the status line"
    )


def test_status_line_shows_scope_not_project_wikilink(
    fake_vault, tmp_path, monkeypatch,
) -> None:
    """The status line's identity bit should be the scope (routing
    identity the user typed at attach), not `[[project-name]]`. The
    wikilink reads as a wiki citation; the scope tells the user where
    they are in the scope tree.
    """
    vault, wiki = fake_vault
    repo_dir = tmp_path / "data-transfer"
    repo_dir.mkdir()
    _register_attachment(
        vault, repo_dir, wiki="ccat", scope="ccat:data-center:data-transfer",
    )
    (repo_dir / ".lore.yml").write_text(
        "wiki: ccat\nscope: ccat:data-center:data-transfer\nbackend: github\n"
    )
    # Drop a project note so project_entry would otherwise win the slot.
    (wiki / "data-transfer.md").write_text(
        "---\ntype: project\nrepo: ccatobs/data-transfer\n---\n\nbody\n"
    )
    monkeypatch.setattr(session_start, "current_repo", lambda _cwd: "ccatobs/data-transfer")

    out = hooks._session_start(str(repo_dir))
    status_line = out.splitlines()[0]
    assert "ccat:data-center:data-transfer" in status_line, status_line
    # Project wikilink must not appear as the identity bit on the
    # status line — only `[[data-transfer]]` would (the project note's
    # name). The "## Focus: [[…]]" block is a separate, deeper section.
    assert "· [[data-transfer]]" not in status_line, status_line


def test_session_start_never_shows_issue_or_pr_counts(fake_vault, tmp_path, monkeypatch):
    """The banner never fetches or renders issue/PR counts — that
    ambient gh fetch was dropped from SessionStart entirely."""
    vault, wiki = fake_vault
    repo_dir = tmp_path / "data-transfer"
    repo_dir.mkdir()
    _register_attachment(vault, repo_dir, wiki="ccat", scope="ccat:data-center:data-transfer")
    (repo_dir / ".lore.yml").write_text(
        "wiki: ccat\nscope: ccat:data-center:data-transfer\nbackend: github\n"
        "issues: --assignee @me --state open\nprs: --author @me\n"
    )
    monkeypatch.setattr(session_start, "current_repo", lambda _cwd: "ccatobs/data-transfer")

    from lore_core import gh as gh_mod

    calls: list[tuple] = []
    monkeypatch.setattr(gh_mod, "run_gh", lambda *a, **kw: calls.append((a, kw)) or [])

    out = hooks._session_start(str(repo_dir))
    assert ": active" in out
    assert calls == [], "SessionStart must never call gh, even with issues/prs configured"
    status_line = out.splitlines()[0]
    assert "issue" not in status_line
    assert "PR" not in status_line


def test_session_start_no_lore_config_emits_attach_hint(fake_vault, tmp_path, monkeypatch):
    """Repo without a `## Lore` block surfaces a clear install/attach
    instruction instead of guessing a wiki (PR 5 of #80 deleted the
    legacy repo→wiki resolver + single-wiki fallback)."""
    vault, wiki = fake_vault
    repo_dir = tmp_path / "no-lore"
    repo_dir.mkdir()
    # No CLAUDE.md at all
    monkeypatch.setattr(session_start, "current_repo", lambda _cwd: None)

    out = hooks._session_start(str(repo_dir))
    assert "no `## Lore` attach block" in out
    assert "lore install" in out
    # AC2 regression guard: an unattached repo's banner must not gain
    # the writing-rules directive — only attached repos get it.
    assert "## Directive" not in out
    assert "writing-rules" not in out


def test_session_start_no_vault_emits_no_vault_hint(tmp_path, monkeypatch):
    """No wiki mount at all (``$LORE_ROOT`` unset/missing) → the "no
    vault" hint, and — like the other unattached paths — no directive."""
    monkeypatch.setenv("LORE_ROOT", str(tmp_path / "does-not-exist"))
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    out = hooks._session_start(str(repo_dir))
    assert out.startswith("lore: no vault at")
    assert "## Directive" not in out
    assert "writing-rules" not in out


def test_session_start_from_lore_missing_wiki_emits_attach_hint(
    fake_vault, tmp_path, monkeypatch,
):
    """`## Lore` block names a wiki that doesn't exist → renderer
    returns None → caller emits the attach hint (no legacy fallback)."""
    vault, wiki = fake_vault
    repo_dir = tmp_path / "bogus"
    repo_dir.mkdir()
    (repo_dir / "CLAUDE.md").write_text(
        "## Lore\n\n- wiki: does-not-exist\n- scope: foo\n"
    )
    monkeypatch.setattr(session_start, "current_repo", lambda _cwd: None)

    out = hooks._session_start(str(repo_dir))
    assert "no `## Lore` attach block" in out
    assert "## Directive" not in out
    assert "writing-rules" not in out
