"""Tests for `lore_core.session.scaffold()` + the `lore session` CLI."""

from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path
from textwrap import dedent

import pytest
from lore_cli import session_cmd
from lore_core.session import format_frontmatter, scaffold, slugify


@pytest.fixture
def solo_vault(tmp_path, monkeypatch):
    """Vault with one wiki, no _users.yml, no `## Lore` config."""
    vault_root = tmp_path / "vault"
    wiki = vault_root / "wiki" / "ccat"
    (wiki / "sessions").mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(vault_root))
    return vault_root, wiki


@pytest.fixture
def attached_project(tmp_path, solo_vault, monkeypatch):
    """A working repo registered as an attachment (Phase 6 registry)."""
    from datetime import UTC, datetime as _dt
    from lore_core.state.attachments import Attachment, AttachmentsFile

    lore_root, wiki = solo_vault
    project = tmp_path / "myproject"
    project.mkdir()

    (lore_root / ".lore").mkdir(parents=True, exist_ok=True)
    af = AttachmentsFile(lore_root); af.load()
    af.add(Attachment(
        path=project, wiki=wiki.name, scope="ccat:data-center",
        attached_at=_dt.now(UTC), source="manual",
    ))
    af.save()
    # Make it a real git repo so current_repo() works in scaffold
    subprocess.run(["git", "init", "-q"], cwd=str(project), check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/ccatobs/data-transfer.git"],
        cwd=str(project),
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "alice@example.org"],
        cwd=str(project),
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "Alice"], cwd=str(project), check=True)
    return project


def test_slugify_basic():
    assert slugify("Fix Retry Logic") == "fix-retry-logic"
    assert slugify("multi  word--with__chars!?") == "multi-word-with-chars"
    assert slugify("a" * 200).startswith("aaa")
    assert len(slugify("a" * 200)) <= 60


def test_format_frontmatter_serializes_lists_inline():
    yaml = format_frontmatter({"tags": ["a", "b", "c"]})
    assert "tags: [a, b, c]" in yaml
    assert yaml.startswith("---") and yaml.endswith("---")


def test_format_frontmatter_drops_empty_values():
    yaml = format_frontmatter({"a": "v", "b": None, "c": "", "d": []})
    assert "a: v" in yaml
    assert "b:" not in yaml.replace("a: v", "")
    assert "c:" not in yaml.replace("a: v", "")


def test_scaffold_solo_no_attach(solo_vault, attached_project):
    """No attachment in the registry → falls back to wiki resolution + wiki=scope."""
    # Strip the registry attachment so we exercise the fallback
    from lore_core.state.attachments import AttachmentsFile
    from lore_core.config import get_lore_root
    _af = AttachmentsFile(get_lore_root()); _af.load()
    _af.remove(attached_project)
    _af.save()
    result = scaffold(
        cwd=attached_project,
        slug="fix-retry",
        description="Fixed retry timeout",
        when=date(2026, 4, 17),
    )
    assert "error" not in result
    assert result["wiki"] == "ccat"
    assert result["scope"] == "ccat"  # wiki-default
    assert result["team_mode"] is False
    assert result["note_path"].endswith("/sessions/2026/04/17-fix-retry.md")
    assert result["frontmatter"]["schema_version"] == 2
    assert result["frontmatter"]["repos"] == ["ccatobs/data-transfer"]
    assert "schema_version: 2" in result["frontmatter_yaml"]
    assert result["existing"] is False


def test_scaffold_uses_lore_block_for_routing_and_scope(solo_vault, attached_project):
    result = scaffold(
        cwd=attached_project,
        slug="dex",
        description="Migrated dex",
        when=date(2026, 4, 17),
    )
    assert result["wiki"] == "ccat"  # from `## Lore` block
    assert result["scope"] == "ccat:data-center"  # from `## Lore` block


def test_scaffold_team_mode_shards_path(solo_vault, attached_project):
    """`_users.yml` flips to sharded sessions/<handle>/ paths."""
    _, wiki = solo_vault
    (wiki / "_users.yml").write_text(
        dedent(
            """\
            users:
              - handle: alice
                aliases:
                  emails:
                    - alice@example.org
            """
        )
    )
    result = scaffold(
        cwd=attached_project,
        slug="t",
        description="d",
        when=date(2026, 4, 17),
    )
    assert result["team_mode"] is True
    assert result["handle"] == "alice"
    assert result["note_path"].endswith("/sessions/alice/2026/04/17-t.md")


def test_scaffold_explicit_overrides(solo_vault, attached_project):
    result = scaffold(
        cwd=attached_project,
        slug="x",
        description="d",
        title="Custom Title",
        tags=["topic/x", "domain/y"],
        implements=["proposal-a"],
        loose_ends=["one", "two"],
        project="myproj",
        when=date(2026, 4, 17),
    )
    fm = result["frontmatter"]
    assert fm["tags"] == ["topic/x", "domain/y"]
    assert fm["implements"] == ["proposal-a"]
    assert fm["loose_ends"] == ["one", "two"]
    assert fm["project"] == "myproj"
    assert "# Session: Custom Title" in result["body_template"]


def test_scaffold_error_when_no_wiki_and_multiple_present(tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    (vault_root / "wiki" / "a" / "sessions").mkdir(parents=True)
    (vault_root / "wiki" / "b" / "sessions").mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(vault_root))
    project = tmp_path / "p"
    project.mkdir()
    result = scaffold(cwd=project, slug="x", description="d")
    assert "error" in result


def test_cli_session_new_dry_run(solo_vault, attached_project, capsys):
    rc = session_cmd.main(
        [
            "new",
            "--cwd",
            str(attached_project),
            "--slug",
            "test",
            "--description",
            "d",
            "--dry-run",
            "--json",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    envelope = json.loads(out)
    assert envelope["schema"] == "lore.session.new/1"
    assert envelope["data"]["dry_run"] is True
    assert envelope["data"]["wiki"] == "ccat"
    # Note must not have been written
    assert not Path(envelope["data"]["note_path"]).exists()


def test_cli_session_new_writes_with_stdin_body(solo_vault, attached_project, capsys, monkeypatch):
    monkeypatch.setattr(
        "sys.stdin",
        type("S", (), {"read": staticmethod(lambda: "# my body\n\nstuff\n")})(),
    )
    rc = session_cmd.main(
        [
            "new",
            "--cwd",
            str(attached_project),
            "--slug",
            "writeit",
            "--description",
            "wrote a note",
            "--body",
            "-",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out.strip()
    note_path = Path(out)
    assert note_path.exists()
    content = note_path.read_text()
    assert "schema_version: 2" in content
    # scope contains `:` so YAML quotes it
    assert 'scope: "ccat:data-center"' in content or "scope: ccat:data-center" in content
    assert "user: alice@example.org" in content or "user: alice" in content
    assert "# my body" in content
    assert "stuff" in content


def test_cli_session_commit(solo_vault, attached_project, monkeypatch):
    """End-to-end: scaffold + write, then commit in the wiki repo."""
    _, wiki = solo_vault
    # Wiki must be a git repo for commit to work
    subprocess.run(["git", "init", "-q"], cwd=str(wiki), check=True)
    subprocess.run(
        ["git", "config", "user.email", "ci@example.org"],
        cwd=str(wiki),
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "ci"], cwd=str(wiki), check=True)

    monkeypatch.setattr(
        "sys.stdin",
        type("S", (), {"read": staticmethod(lambda: "# body\n")})(),
    )
    rc = session_cmd.main(
        [
            "new",
            "--cwd",
            str(attached_project),
            "--slug",
            "commitit",
            "--description",
            "commit test",
            "--body",
            "-",
        ]
    )
    assert rc == 0

    # Find the note that was written
    sessions_dir = wiki / "sessions"
    notes = list(sessions_dir.rglob("*.md"))
    assert len(notes) == 1
    note_path = notes[0]

    rc2 = session_cmd.main(["commit", str(note_path), "--json"])
    assert rc2 == 0

    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=str(wiki),
        capture_output=True,
        text=True,
        check=True,
    )
    assert "lore: session" in log.stdout
