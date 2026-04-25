"""Tests for `lore attachments` CLI (ls / show / rm)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lore_cli.__main__ import app
from lore_core.state.attachments import Attachment, AttachmentsFile

runner = CliRunner(mix_stderr=False)


@pytest.fixture
def lore_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / ".lore").mkdir()
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    return tmp_path


def _seed(lore_root: Path, path: Path, *, wiki: str = "w", scope: str = "a:b") -> None:
    af = AttachmentsFile(lore_root)
    af.load()
    af.add(
        Attachment(
            path=path,
            wiki=wiki,
            scope=scope,
            attached_at=datetime(2026, 4, 22, 9, 0, tzinfo=UTC),
            source="manual",
        )
    )
    af.save()


def test_ls_empty(lore_root: Path) -> None:
    result = runner.invoke(app, ["attachments", "ls"])
    assert result.exit_code == 0
    assert "No attachments" in result.stdout


def test_ls_shows_entries(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed(lore_root, repo, wiki="ccat", scope="ccat:ds")

    result = runner.invoke(app, ["attachments", "ls"])
    assert result.exit_code == 0
    # Rich may truncate the path; use JSON for structural checks
    json_result = runner.invoke(app, ["attachments", "ls", "--json"])
    assert json_result.exit_code == 0
    payload = json.loads(json_result.stdout)
    assert len(payload["data"]) == 1
    assert payload["data"][0]["wiki"] == "ccat"
    assert payload["data"][0]["scope"] == "ccat:ds"
    # Sanity: table rendered something referencing the scope
    assert "ccat:ds" in result.stdout


def test_ls_json(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed(lore_root, repo, wiki="ccat", scope="ccat:ds")

    result = runner.invoke(app, ["attachments", "ls", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema"] == "lore.attachments.ls/1"
    assert len(payload["data"]) == 1
    assert payload["data"][0]["wiki"] == "ccat"


def test_show_hits(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "sub").mkdir(parents=True)
    _seed(lore_root, repo, wiki="w", scope="a:b")

    result = runner.invoke(app, ["attachments", "show", str(repo / "sub"), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["data"]["scope"] == "a:b"
    assert payload["data"]["wiki"] == "w"


def test_show_miss_exits_1(lore_root: Path, tmp_path: Path) -> None:
    stranger = tmp_path / "stranger"
    stranger.mkdir()
    result = runner.invoke(app, ["attachments", "show", str(stranger)])
    assert result.exit_code == 1


def test_show_json(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed(lore_root, repo)
    result = runner.invoke(app, ["attachments", "show", str(repo), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["data"]["wiki"] == "w"


def test_rm_removes(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed(lore_root, repo)
    result = runner.invoke(app, ["attachments", "rm", str(repo)])
    assert result.exit_code == 0

    af = AttachmentsFile(lore_root)
    af.load()
    assert af.all() == []


def test_rm_missing_exits_1(lore_root: Path, tmp_path: Path) -> None:
    result = runner.invoke(app, ["attachments", "rm", str(tmp_path / "ghost")])
    assert result.exit_code == 1


def test_ls_without_lore_root_env_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LORE_ROOT", raising=False)
    result = runner.invoke(app, ["attachments", "ls"])
    # Exit 2 = "incorrect usage / configuration error" (argparse convention).
    # Standardised in Phase 2 when the per-command _lore_root_or_die helpers
    # were consolidated into lore_cli._cli_helpers.lore_root_or_die.
    assert result.exit_code == 2


# ---- purge-unattached ----

def _seed_ledger_entry(lore_root: Path, directory: Path) -> None:
    """Insert a pending transcript-ledger entry pointing at `directory`."""
    from lore_core.ledger import TranscriptLedger, TranscriptLedgerEntry

    ledger = TranscriptLedger(lore_root)
    entry = TranscriptLedgerEntry(
        host="claude-code",
        transcript_id=f"test-{directory.name}",
        path=directory / "transcript.jsonl",
        directory=directory,
        digested_hash=None,
        digested_index_hint=None,
        synthesised_hash=None,
        last_mtime=datetime(2026, 4, 22, 9, 0, tzinfo=UTC),
        curator_a_run=None,
        noteworthy=None,
        session_note=None,
    )
    ledger.upsert(entry)


def test_purge_unattached_empty(lore_root: Path) -> None:
    result = runner.invoke(app, ["attachments", "purge-unattached"])
    assert result.exit_code == 0
    assert "Nothing to purge" in result.stdout


def test_purge_unattached_dry_run(lore_root: Path, tmp_path: Path) -> None:
    unattached_dir = tmp_path / "unattached-repo"
    unattached_dir.mkdir()
    _seed_ledger_entry(lore_root, unattached_dir)

    result = runner.invoke(app, ["attachments", "purge-unattached", "--dry-run"])
    assert result.exit_code == 0
    assert "Dry-run" in result.stdout
    # Ledger entry still pending
    from lore_core.ledger import TranscriptLedger
    assert len(TranscriptLedger(lore_root).pending()) == 1


def test_purge_unattached_applies(lore_root: Path, tmp_path: Path) -> None:
    unattached_dir = tmp_path / "unattached-repo"
    unattached_dir.mkdir()
    _seed_ledger_entry(lore_root, unattached_dir)

    result = runner.invoke(app, ["attachments", "purge-unattached", "--yes"])
    assert result.exit_code == 0

    from lore_core.ledger import TranscriptLedger
    # After purge, pending is empty (orphan=True excludes from pending)
    assert TranscriptLedger(lore_root).pending() == []
