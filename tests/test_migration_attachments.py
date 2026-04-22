"""Tests for the legacy-CLAUDE.md-to-registry migration (Phase 5)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lore_cli.__main__ import app
from lore_core.migration import MigrationResult, migrate_repo
from lore_core.offer import FILENAME as LORE_YML
from lore_core.offer import parse_lore_yml
from lore_core.state.attachments import AttachmentsFile
from lore_core.state.scopes import ScopesFile


runner = CliRunner(mix_stderr=False)

LEGACY_BLOCK = """# My Project

Some project-specific prose the user wrote.

## Lore

<!-- Managed by /lore:attach -->

- wiki: team-alpha
- scope: ccat:data-center:computers
- backend: github
- issues: --assignee @me --state open

## Other section

Unrelated content Lore must not touch.
"""


@pytest.fixture
def lore_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / ".lore").mkdir()
    (tmp_path / "wiki" / "team-alpha").mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    return tmp_path


# ---- core migrate_repo ----

def test_migrate_happy_path(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text(LEGACY_BLOCK)

    result = migrate_repo(repo, lore_root=lore_root)
    assert result.action == "migrated"
    assert result.wrote_lore_yml
    assert result.wrote_attachment
    assert result.stripped_claude_md

    # .lore.yml written with the right fields
    offer = parse_lore_yml(repo / LORE_YML)
    assert offer is not None
    assert offer.wiki == "team-alpha"
    assert offer.scope == "ccat:data-center:computers"
    assert offer.backend == "github"
    assert offer.issues == "--assignee @me --state open"

    # Attachment row present with source=migrated + fingerprint
    af = AttachmentsFile(lore_root); af.load()
    rows = af.all()
    assert len(rows) == 1
    assert rows[0].source == "migrated"
    assert rows[0].offer_fingerprint is not None

    # Scope chain ingested
    sf = ScopesFile(lore_root); sf.load()
    assert sf.get("ccat") is not None
    assert sf.get("ccat:data-center") is not None
    assert sf.get("ccat:data-center:computers") is not None

    # CLAUDE.md stripped but surrounding content preserved
    remaining = (repo / "CLAUDE.md").read_text()
    assert "## Lore" not in remaining
    assert "Some project-specific prose" in remaining
    assert "## Other section" in remaining
    assert "Unrelated content Lore must not touch." in remaining


def test_migrate_no_block_is_noop(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text("# No lore here\n")

    result = migrate_repo(repo, lore_root=lore_root)
    assert result.action == "no-block"
    af = AttachmentsFile(lore_root); af.load()
    assert af.all() == []


def test_migrate_idempotent(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text(LEGACY_BLOCK)

    first = migrate_repo(repo, lore_root=lore_root)
    assert first.action == "migrated"

    # Second run — CLAUDE.md block is gone; should be no-block
    second = migrate_repo(repo, lore_root=lore_root)
    assert second.action == "no-block"

    # Only one attachment
    af = AttachmentsFile(lore_root); af.load()
    assert len(af.all()) == 1


def test_migrate_idempotent_with_existing_lore_yml(lore_root: Path, tmp_path: Path) -> None:
    """If .lore.yml already exists with matching wiki+scope, migration is a no-op."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text(LEGACY_BLOCK)
    (repo / LORE_YML).write_text(
        "wiki: team-alpha\nscope: ccat:data-center:computers\n"
    )

    result = migrate_repo(repo, lore_root=lore_root)
    assert result.action == "already"


def test_migrate_dry_run(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text(LEGACY_BLOCK)

    result = migrate_repo(repo, lore_root=lore_root, dry_run=True)
    assert result.action == "migrated"
    assert not result.wrote_lore_yml
    assert not result.wrote_attachment
    assert not (repo / LORE_YML).exists()
    af = AttachmentsFile(lore_root); af.load()
    assert af.all() == []


def test_migrate_scope_conflict(lore_root: Path, tmp_path: Path) -> None:
    """Existing `ccat` root with a different wiki → skipped."""
    sf = ScopesFile(lore_root); sf.load()
    sf.ingest_chain("ccat:existing", "other-wiki")
    sf.save()

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text(LEGACY_BLOCK)

    result = migrate_repo(repo, lore_root=lore_root)
    assert result.action == "skipped"
    assert "scope conflict" in result.detail.lower()


# ---- CLI ----

def test_cli_migrate_dry_run(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text(LEGACY_BLOCK)

    result = runner.invoke(
        app,
        ["migrate", "attachments", "--repo", str(repo), "--dry-run"],
    )
    assert result.exit_code == 0, result.stdout
    assert "migrated" in result.stdout
    # No side effects
    assert not (repo / LORE_YML).exists()


def test_cli_migrate_applies(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text(LEGACY_BLOCK)

    result = runner.invoke(
        app,
        ["migrate", "attachments", "--repo", str(repo), "--yes"],
    )
    assert result.exit_code == 0, result.stdout + result.stderr

    assert (repo / LORE_YML).exists()
    af = AttachmentsFile(lore_root); af.load()
    assert len(af.all()) == 1


def test_cli_migrate_no_lore_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LORE_ROOT", str(tmp_path / "missing"))
    result = runner.invoke(app, ["migrate", "attachments", "--dry-run"])
    assert result.exit_code == 1


# ---- lazy fallback in legacy resolver ----

def test_lazy_migration_fires_when_flag_set(
    lore_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invoking the legacy walk-up resolver triggers migration when
    LORE_NEW_STATE=1."""
    from lore_core.scope_resolver import _legacy_walk_up_resolve

    monkeypatch.setenv("LORE_NEW_STATE", "1")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text(LEGACY_BLOCK)

    scope = _legacy_walk_up_resolve(repo)
    assert scope is not None
    assert scope.wiki == "team-alpha"

    # Side effect: .lore.yml written, attachment registered
    assert (repo / LORE_YML).exists()
    af = AttachmentsFile(lore_root); af.load()
    assert len(af.all()) == 1

    # Hook event logged
    events = (lore_root / ".lore" / "hook-events.jsonl").read_text().splitlines()
    matching = [json.loads(l) for l in events if l]
    assert any(r.get("event") == "attachments-migrated-lazy" for r in matching)


def test_lazy_migration_does_not_fire_when_flag_unset(
    lore_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lore_core.scope_resolver import _legacy_walk_up_resolve

    monkeypatch.delenv("LORE_NEW_STATE", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text(LEGACY_BLOCK)

    _legacy_walk_up_resolve(repo)
    assert not (repo / LORE_YML).exists()
