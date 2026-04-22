"""Tests for the Phase 4 doctor extensions (attachments, scope tree, ledger)."""

from __future__ import annotations

import json
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
    # Real wiki dir so doctor's wiki check passes
    (tmp_path / ".lore").mkdir()
    (tmp_path / "wiki" / "private").mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    return tmp_path


def _attach(path: Path, *, wiki: str = "private", scope: str = "lore:a",
            fp: str | None = None) -> Attachment:
    return Attachment(
        path=path,
        wiki=wiki,
        scope=scope,
        attached_at=datetime(2026, 4, 22, 9, 0, tzinfo=UTC),
        source="manual",
        offer_fingerprint=fp,
    )


def _seed_healthy(lore_root: Path, tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    af = AttachmentsFile(lore_root); af.load()
    af.add(_attach(repo, wiki="private", scope="lore:a"))
    af.save()
    sf = ScopesFile(lore_root); sf.load()
    sf.ingest_chain("lore:a", "private")
    sf.save()
    return repo


def test_doctor_attachments_healthy(lore_root: Path, tmp_path: Path) -> None:
    _seed_healthy(lore_root, tmp_path)
    result = runner.invoke(app, ["doctor", "--json"])
    payload = json.loads(result.stdout)
    checks = {c["check"]: c for c in payload["data"]["checks"]}
    assert checks["attachments"]["ok"] is True
    assert "1 attachment" in checks["attachments"]["message"]


def test_doctor_attachments_missing_path(lore_root: Path, tmp_path: Path) -> None:
    """Attachment pointing at a deleted directory."""
    ghost = tmp_path / "ghost"
    af = AttachmentsFile(lore_root); af.load()
    af.add(_attach(ghost))
    af.save()
    # scope present so only the path is an issue
    sf = ScopesFile(lore_root); sf.load()
    sf.ingest_chain("lore:a", "private")
    sf.save()

    result = runner.invoke(app, ["doctor", "--json"])
    payload = json.loads(result.stdout)
    checks = {c["check"]: c for c in payload["data"]["checks"]}
    assert checks["attachments"]["ok"] is False
    assert "missing on disk" in checks["attachments"]["message"]


def test_doctor_attachments_scope_missing_in_tree(lore_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    af = AttachmentsFile(lore_root); af.load()
    af.add(_attach(repo, scope="orphan:scope"))
    af.save()
    # Don't seed scopes.json

    result = runner.invoke(app, ["doctor", "--json"])
    payload = json.loads(result.stdout)
    checks = {c["check"]: c for c in payload["data"]["checks"]}
    assert checks["attachments"]["ok"] is False
    assert "not in scopes.json" in checks["attachments"]["message"]


def test_doctor_scope_tree_missing_parent(lore_root: Path, tmp_path: Path) -> None:
    """Directly write a scopes.json entry without its parent → integrity issue."""
    sf = ScopesFile(lore_root); sf.load()
    # Bypass ingest_chain to create the broken state
    from lore_core.state.scopes import ScopeEntry
    sf.set_entry("a:orphan", ScopeEntry())
    sf.save()

    result = runner.invoke(app, ["doctor", "--json"])
    payload = json.loads(result.stdout)
    checks = {c["check"]: c for c in payload["data"]["checks"]}
    assert checks["scope tree"]["ok"] is False
    assert "parent" in checks["scope tree"]["message"]


def test_doctor_scope_tree_no_resolved_wiki(lore_root: Path, tmp_path: Path) -> None:
    """Root scope with no wiki → flagged."""
    sf = ScopesFile(lore_root); sf.load()
    from lore_core.state.scopes import ScopeEntry
    sf.set_entry("rootless", ScopeEntry())    # no wiki
    sf.save()

    result = runner.invoke(app, ["doctor", "--json"])
    payload = json.loads(result.stdout)
    checks = {c["check"]: c for c in payload["data"]["checks"]}
    assert checks["scope tree"]["ok"] is False
    assert "no resolved wiki" in checks["scope tree"]["message"]


def test_doctor_ledger_buckets_informational(lore_root: Path, tmp_path: Path) -> None:
    _seed_healthy(lore_root, tmp_path)
    result = runner.invoke(app, ["doctor", "--json"])
    payload = json.loads(result.stdout)
    checks = {c["check"]: c for c in payload["data"]["checks"]}
    # Ledger bucket check always OK (informational), message describes counts
    assert checks["ledger buckets"]["ok"] is True
