"""Tests for the schema-v2 SessionStart path.

Covers the pure helpers (scope-tree walk, walk-up of `CLAUDE.md`) and the
orchestrator. SessionStart itself never calls `gh` — issue/PR counts were
dropped from the banner, and the list wrappers and line formatters that
served them were deleted once nothing else reached them.
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
    """_resolve_attach_block is a thin wrapper over the route. It returns
    the synthetic <repo>/CLAUDE.md sentinel and a block dict."""
    import subprocess

    from conftest import seed_scope
    from lore_core.session import _resolve_attach_block

    parent = tmp_path / "repo"
    child = parent / "sub" / "deep"
    child.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(parent)], check=True)

    (tmp_path / ".lore").mkdir()
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    seed_scope(parent, "ccat", name="ccat:data-center:data-transfer")

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
    """Route ``repo`` to ``wiki`` with a scope in the person config (PRD 0016)."""
    from conftest import seed_scope

    (lore_root / ".lore").mkdir(exist_ok=True)
    seed_scope(repo, wiki, name=scope)


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
    """The banner never fetches or renders issue/PR counts — that ambient gh
    fetch was dropped from SessionStart entirely.

    Guarded at the subprocess boundary rather than at a named helper: the
    list wrappers the banner used are gone, and the invariant is that
    SessionStart shells out to gh at all, not that one function stays unused.
    A `.lore.yml` left over from an older Lore still carries the filter keys;
    they are inert, and reaching them must not resurrect the fetch."""
    vault, wiki = fake_vault
    repo_dir = tmp_path / "data-transfer"
    repo_dir.mkdir()
    _register_attachment(vault, repo_dir, wiki="ccat", scope="ccat:data-center:data-transfer")
    (repo_dir / ".lore.yml").write_text(
        "wiki: ccat\nscope: ccat:data-center:data-transfer\nbackend: github\n"
        "issues: --assignee @me --state open\nprs: --author @me\n"
    )
    monkeypatch.setattr(session_start, "current_repo", lambda _cwd: "ccatobs/data-transfer")

    import subprocess as subprocess_mod

    from lore_core import gh as gh_mod

    calls: list[list] = []
    real_run = subprocess_mod.run

    def spy(cmd, *a, **kw):
        calls.append(list(cmd))
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(gh_mod.subprocess, "run", spy)

    out = hooks._session_start(str(repo_dir))
    assert ": active" in out
    assert calls == [], "SessionStart must never shell out to gh"
    status_line = out.splitlines()[0]
    assert "issue" not in status_line
    assert "PR" not in status_line


def test_session_start_no_scope_emits_setup_scope_hint(fake_vault, tmp_path, monkeypatch):
    """A repo no scope covers surfaces `lore setup scope` instead of
    guessing a wiki (PR 5 of #80 deleted the single-wiki fallback)."""
    vault, wiki = fake_vault
    repo_dir = tmp_path / "no-lore"
    repo_dir.mkdir()
    # No CLAUDE.md at all
    monkeypatch.setattr(session_start, "current_repo", lambda _cwd: None)

    out = hooks._session_start(str(repo_dir))
    assert "no scope routes this repo" in out
    assert "lore setup scope" in out
    # AC2 regression guard: an unattached repo's banner must not gain
    # the writing-rules directive — only attached repos get it.
    assert "## Directive" not in out
    assert "writing-rules" not in out


def test_session_start_names_the_scope_and_the_wiki_the_vault_does_not_hold(
    fake_vault, tmp_path, monkeypatch
):
    """Issue #575: a scope that names a missing wiki is not "no scope"."""
    vault, _wiki = fake_vault
    repo_dir = tmp_path / "lore"
    repo_dir.mkdir()
    _register_attachment(vault, repo_dir, wiki="private", scope="lore")
    monkeypatch.setattr(session_start, "current_repo", lambda _cwd: None)

    out = hooks._session_start(str(repo_dir))
    assert "scope `lore`" in out and "wiki `private`" in out
    assert "lore setup scope" in out
    assert "no scope routes this repo" not in out


def test_session_start_emits_the_text_whole_and_context_log_prints_the_same(
    tmp_path, monkeypatch, capsys
):
    """Issue #575: Lore cuts no SessionStart text."""
    import json
    import os

    monkeypatch.setenv("LORE_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(hooks, "_claude_code_pid", os.getpid)  # a live pid survives the gc
    text = "lore 9.9.9: active\n" + "x" * 20_000 + "\nlast line"
    hooks._emit("SessionStart", text, plain=False)
    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert context == text
    assert hooks._context_log().rstrip("\n").endswith(text)


def test_the_project_orientation_gives_way_over_the_hook_limit():
    """Claude Code limits a hook string to 10,000 characters (issue #575)."""
    orientation = "## Project: [[lore]]\n" + "o" * 2_900
    rest = "## Index (`lore index`)\n" + "i" * 1_000
    small = f"banner\n\n{orientation}\n\n{rest}"
    assert session_start.fit_hook_limit(small, orientation) == small
    big = f"banner\n\n{orientation}\n\n{'p' * 7_000}\n\n{rest}"
    out = session_start.fit_hook_limit(big, orientation)
    assert len(out) <= session_start.HOOK_LIMIT_CHARS
    assert "## Project: [[lore]]" in out and "ooo" not in out
    assert out.endswith(rest) and "p" * 7_000 in out
    assert session_start.fit_hook_limit(big, None) == big


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


def test_session_start_missing_wiki_emits_setup_scope_hint(
    fake_vault, tmp_path, monkeypatch,
):
    """A scope names a wiki that doesn't exist → renderer returns None →
    caller emits the setup hint (no legacy fallback)."""
    from conftest import seed_scope

    vault, wiki = fake_vault
    repo_dir = tmp_path / "bogus"
    repo_dir.mkdir()
    seed_scope(repo_dir, "does-not-exist")
    monkeypatch.setattr(session_start, "current_repo", lambda _cwd: None)

    out = hooks._session_start(str(repo_dir))
    assert "lore setup scope" in out
    assert "## Directive" not in out
    assert "writing-rules" not in out


def test_folder_scope_banner_holds_the_repo_project_orientation(
    fake_vault, tmp_path, monkeypatch, capsys,
) -> None:
    """A folder scope (PRD 0016) covers many repos: the project slug is the
    repo's folder name, not the scope name (#479 review)."""
    import subprocess

    from conftest import seed_scope

    vault, wiki = fake_vault
    root = tmp_path / "data-center"
    repo = root / "ops-db"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    seed_scope(root, "ccat", name="data-center")
    note = wiki / "projects" / "ops-db" / "ops-db.md"
    note.parent.mkdir(parents=True)
    note.write_text("---\ntype: project\n---\n\n# Project: ops-db\n\nOps DB orientation body.\n")
    monkeypatch.setattr(session_start, "current_repo", lambda _cwd: "ccatobs/ops-db")
    for name in dir(hooks):  # no detached children from this test
        if name.startswith("_spawn_"):
            monkeypatch.setattr(hooks, name, lambda *a, **k: None)
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(""))

    hooks.cmd_session_start(cwd=str(repo), plain=True, probe=False)

    assert "Ops DB orientation body." in capsys.readouterr().out
