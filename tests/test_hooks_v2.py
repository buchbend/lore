"""Tests for the schema-v2 SessionStart path.

Covers the pure helpers (scope-tree walk, filter parsing, walk-up of
`CLAUDE.md`) and the orchestrator with `gh` mocked out.
"""

from __future__ import annotations

import pytest
from lore_cli import hooks

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


# ---------- _split_filter ----------


@pytest.mark.parametrize("raw,expected", [
    ("", []),
    ("--assignee @me", ["--assignee", "@me"]),
    ("--assignee @me --state open", ["--assignee", "@me", "--state", "open"]),
    ('--label "needs triage"', ["--label", "needs triage"]),
    (None, []),
])
def test_split_filter(raw, expected):
    assert hooks._split_filter(raw) == expected


def test_split_filter_malformed_falls_back_to_whitespace():
    # Unterminated quote — shlex raises, fallback kicks in
    assert hooks._split_filter('--label "oops') == ["--label", '"oops']


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
    assert hooks._format_issue_line({"number": 47, "title": "retry cap"}) == "- #47 retry cap"


def test_format_pr_line_draft():
    assert hooks._format_pr_line(
        {"number": 31, "title": "atm-table v2", "isDraft": True}
    ) == "- #31 [draft] atm-table v2"


def test_format_pr_line_ready():
    assert hooks._format_pr_line(
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


def _fake_gh_factory(responses: dict[tuple[str, str], list[dict]]):
    """Build a _run_gh stub that returns the canned response for (kind, repo)."""
    def _fake(kind, repo, filter_args):
        return responses.get((kind, repo), [])
    return _fake


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
    # Write a .lore.yml so the backend/issues/prs fields surface to
    # _session_start_from_lore (block dict merge in _resolve_attach_block).
    (repo_dir / ".lore.yml").write_text(
        "wiki: ccat\nscope: ccat:data-center:data-transfer\nbackend: github\n"
        "issues: --assignee @me --state open\nprs: --author @me\n"
    )
    monkeypatch.setattr(hooks, "current_repo", lambda _cwd: "ccatobs/data-transfer")
    monkeypatch.setattr(
        hooks,
        "_run_gh",
        _fake_gh_factory({
            ("issue", "ccatobs/data-transfer"): [
                {"number": 47, "title": "retry cap missing", "state": "OPEN"},
                {"number": 52, "title": "stale docs", "state": "OPEN"},
            ],
            ("issue", "ccatobs/system-integration"): [
                {"number": 10, "title": "trust CA", "state": "OPEN"},
            ],
            ("pr", "ccatobs/data-transfer"): [
                {"number": 31, "title": "atm-table v2", "isDraft": True},
            ],
        }),
    )

    out = hooks._session_start(str(repo_dir))
    assert "ccat:data-center:data-transfer" in out
    assert "14 notes" in out
    assert "2 issues" in out
    assert "1 PR" in out
    assert "#47 retry cap missing" in out
    assert "#52 stale docs" in out
    assert "#31 [draft] atm-table v2" in out
    # Subtree aggregation: 1 sibling issue
    assert "+1 from `ccat:data-center` subtree" in out
    assert "/lore:resume ccat:data-center" in out


def test_session_start_from_lore_falls_back_when_gh_fails(fake_vault, tmp_path, monkeypatch):
    vault, wiki = fake_vault
    repo_dir = tmp_path / "data-transfer"
    repo_dir.mkdir()
    _register_attachment(vault, repo_dir, wiki="ccat", scope="ccat:data-center:data-transfer")
    (repo_dir / ".lore.yml").write_text(
        "wiki: ccat\nscope: ccat:data-center:data-transfer\nbackend: github\n"
    )
    monkeypatch.setattr(hooks, "current_repo", lambda _cwd: "ccatobs/data-transfer")
    # gh returns nothing for everything
    monkeypatch.setattr(hooks, "_run_gh", lambda *a, **kw: [])

    out = hooks._session_start(str(repo_dir))
    # Status line still renders
    assert "lore: loaded" in out
    # No issues → placeholder line
    assert "No open issues matched your filters" in out


def test_session_start_no_lore_config_uses_legacy_path(fake_vault, tmp_path, monkeypatch):
    vault, wiki = fake_vault
    repo_dir = tmp_path / "no-lore"
    repo_dir.mkdir()
    # No CLAUDE.md at all
    monkeypatch.setattr(hooks, "current_repo", lambda _cwd: None)

    out = hooks._session_start(str(repo_dir))
    # Should hit the legacy branch — emits a resolved-wiki status line
    # (single wiki in vault → auto-selected)
    assert "lore: loaded" in out or "no wiki resolved" in out


def test_session_start_from_lore_missing_wiki_falls_through(fake_vault, tmp_path, monkeypatch):
    vault, wiki = fake_vault
    repo_dir = tmp_path / "bogus"
    repo_dir.mkdir()
    (repo_dir / "CLAUDE.md").write_text(
        "## Lore\n\n- wiki: does-not-exist\n- scope: foo\n"
    )
    monkeypatch.setattr(hooks, "current_repo", lambda _cwd: None)
    monkeypatch.setattr(hooks, "_run_gh", lambda *a, **kw: [])

    out = hooks._session_start(str(repo_dir))
    # Wiki doesn't exist → _session_start_from_lore returns None →
    # legacy branch kicks in → single wiki in vault is picked
    assert "lore: loaded" in out or "no wiki" in out
