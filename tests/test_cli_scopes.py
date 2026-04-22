"""Tests for `lore scopes` CLI (ls / show / rename / reparent / rm)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lore_cli.__main__ import app
from lore_core.state.attachments import Attachment, AttachmentsFile
from lore_core.state.scopes import ScopesFile

runner = CliRunner(mix_stderr=False)


@pytest.fixture
def lore_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / ".lore").mkdir()
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    return tmp_path


def _seed_scope(lore_root: Path, scope_id: str, wiki: str = "w") -> None:
    sf = ScopesFile(lore_root)
    sf.load()
    sf.ingest_chain(scope_id, wiki)
    sf.save()


def _seed_attachment(lore_root: Path, path: Path, *, wiki: str, scope: str) -> None:
    af = AttachmentsFile(lore_root)
    af.load()
    af.add(Attachment(
        path=path,
        wiki=wiki,
        scope=scope,
        attached_at=datetime(2026, 4, 22, 9, 0, tzinfo=UTC),
        source="manual",
    ))
    af.save()


def test_ls_empty(lore_root: Path) -> None:
    result = runner.invoke(app, ["scopes", "ls"])
    assert result.exit_code == 0
    assert "No scopes" in result.stdout


def test_ls_shows_tree(lore_root: Path) -> None:
    _seed_scope(lore_root, "ccat:data-center:computers", "team-alpha")
    result = runner.invoke(app, ["scopes", "ls"])
    assert result.exit_code == 0
    assert "ccat" in result.stdout
    assert "data-center" in result.stdout
    assert "computers" in result.stdout
    assert "team-alpha" in result.stdout


def test_show_hit(lore_root: Path) -> None:
    _seed_scope(lore_root, "a:b", "w")
    result = runner.invoke(app, ["scopes", "show", "a:b"])
    assert result.exit_code == 0
    assert "a:b" in result.stdout
    assert "w" in result.stdout


def test_show_miss(lore_root: Path) -> None:
    result = runner.invoke(app, ["scopes", "show", "nope"])
    assert result.exit_code == 1


def test_rename_propagates_to_attachments(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_scope(lore_root, "ccat:data-center:computers", "team-alpha")
    _seed_attachment(lore_root, repo, wiki="team-alpha", scope="ccat:data-center:computers")

    result = runner.invoke(
        app,
        ["scopes", "rename", "ccat:data-center", "ccat:infra", "--yes"],
    )
    assert result.exit_code == 0

    # Both files now reflect the rename
    sf = ScopesFile(lore_root); sf.load()
    assert sf.get("ccat:data-center") is None
    assert sf.get("ccat:infra") is not None
    assert sf.get("ccat:infra:computers") is not None

    af = AttachmentsFile(lore_root); af.load()
    entries = af.all()
    assert len(entries) == 1
    assert entries[0].scope == "ccat:infra:computers"


def test_rename_missing_scope_fails(lore_root: Path) -> None:
    result = runner.invoke(app, ["scopes", "rename", "nope", "other", "--yes"])
    assert result.exit_code == 1


def test_reparent_preserves_leaf(lore_root: Path) -> None:
    _seed_scope(lore_root, "a:b:c", "w")
    _seed_scope(lore_root, "x", "w2") if False else None
    # Can't have two roots with different wikis — use same wiki
    sf = ScopesFile(lore_root); sf.load()
    sf.ingest_chain("x", "w")      # sibling root, same wiki
    sf.save()

    result = runner.invoke(app, ["scopes", "reparent", "a:b", "x", "--yes"])
    assert result.exit_code == 0

    sf = ScopesFile(lore_root); sf.load()
    assert sf.get("a:b") is None
    assert sf.get("x:b") is not None
    assert sf.get("x:b:c") is not None


def test_rm_leaf(lore_root: Path) -> None:
    _seed_scope(lore_root, "a:b", "w")
    result = runner.invoke(app, ["scopes", "rm", "a:b"])
    assert result.exit_code == 0
    sf = ScopesFile(lore_root); sf.load()
    assert sf.get("a:b") is None


def test_rm_fails_with_descendants(lore_root: Path) -> None:
    _seed_scope(lore_root, "a:b:c", "w")
    result = runner.invoke(app, ["scopes", "rm", "a:b"])
    assert result.exit_code == 1


def test_rm_fails_with_attachments(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_scope(lore_root, "a:b", "w")
    _seed_attachment(lore_root, repo, wiki="w", scope="a:b")
    result = runner.invoke(app, ["scopes", "rm", "a:b"])
    assert result.exit_code == 1


def test_rm_missing(lore_root: Path) -> None:
    result = runner.invoke(app, ["scopes", "rm", "nope"])
    assert result.exit_code == 1
