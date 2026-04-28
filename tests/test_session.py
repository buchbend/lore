"""Tests for `lore_core.session` (slug + commit) and `lore session commit` CLI.

The `scaffold` / `lore session new` machinery was retired alongside the
manual `/lore:session` skill — auto-capture (curator A → session_writer)
is the canonical write path. This file now covers only the surface that
remained: the `slugify` helper used by plans and the `commit` CLI verb
used by inbox/briefing skills."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from lore_cli import session_cmd
from lore_core.session import slugify


@pytest.fixture
def wiki_repo(tmp_path, monkeypatch):
    """Vault with one wiki initialised as a git repo."""
    vault_root = tmp_path / "vault"
    wiki = vault_root / "wiki" / "ccat"
    (wiki / "sessions").mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(vault_root))

    subprocess.run(["git", "init", "-q"], cwd=str(wiki), check=True)
    subprocess.run(
        ["git", "config", "user.email", "ci@example.org"],
        cwd=str(wiki), check=True,
    )
    subprocess.run(["git", "config", "user.name", "ci"], cwd=str(wiki), check=True)
    return wiki


def test_slugify_basic():
    assert slugify("Fix Retry Logic") == "fix-retry-logic"
    assert slugify("multi  word--with__chars!?") == "multi-word-with-chars"
    assert slugify("a" * 200).startswith("aaa")
    assert len(slugify("a" * 200)) <= 60


def test_cli_session_commit(wiki_repo):
    """`lore session commit` adds + commits a note inside its wiki repo."""
    note = wiki_repo / "sessions" / "2026" / "04" / "28-handwritten.md"
    note.parent.mkdir(parents=True)
    note.write_text("---\ntype: session\n---\n# body\n")

    rc = session_cmd.main(["commit", str(note), "--json"])
    assert rc == 0

    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=str(wiki_repo),
        capture_output=True, text=True, check=True,
    )
    assert "28-handwritten" in log.stdout


def test_cli_session_commit_idempotent(wiki_repo):
    """Re-committing an unchanged note succeeds with no new commit."""
    note = wiki_repo / "sessions" / "2026" / "04" / "28-handwritten.md"
    note.parent.mkdir(parents=True)
    note.write_text("---\ntype: session\n---\n# body\n")

    assert session_cmd.main(["commit", str(note)]) == 0
    assert session_cmd.main(["commit", str(note)]) == 0  # idempotent


def test_cli_session_commit_rejects_path_outside_wiki(tmp_path, monkeypatch):
    """A note that lives outside any wiki under $LORE_ROOT is rejected."""
    vault_root = tmp_path / "vault"
    (vault_root / "wiki").mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(vault_root))

    stray = tmp_path / "stray.md"
    stray.write_text("# stray\n")
    assert session_cmd.main(["commit", str(stray)]) != 0


def test_cli_session_commit_missing_path(tmp_path, monkeypatch):
    """Missing file path exits non-zero."""
    vault_root = tmp_path / "vault"
    (vault_root / "wiki").mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(vault_root))

    assert session_cmd.main(["commit", str(tmp_path / "does-not-exist.md")]) != 0
