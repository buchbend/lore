"""Tests for `lore doctor`."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from lore_cli import doctor_cmd


@pytest.fixture
def healthy_vault(tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    (vault_root / "wiki" / "ccat" / "sessions").mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(vault_root))
    monkeypatch.setenv("LORE_CACHE", str(tmp_path / "cache"))
    return vault_root


def test_doctor_healthy(healthy_vault, capsys):
    rc = doctor_cmd.main(["--cwd", str(healthy_vault), "--json"])
    assert rc == 0
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["schema"] == "lore.doctor/1"
    assert envelope["data"]["ok"] is True
    checks = {c["check"]: c for c in envelope["data"]["checks"]}
    assert checks["LORE_ROOT"]["ok"] is True
    assert checks["wikis"]["ok"] is True
    assert checks["cache"]["ok"] is True
    assert checks["MCP server"]["ok"] is True


def test_doctor_no_wikis_fails(tmp_path, monkeypatch, capsys):
    vault_root = tmp_path / "vault"
    (vault_root / "wiki").mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(vault_root))
    monkeypatch.setenv("LORE_CACHE", str(tmp_path / "cache"))
    rc = doctor_cmd.main(["--cwd", str(vault_root), "--json"])
    assert rc == 1
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["data"]["ok"] is False
    checks = {c["check"]: c for c in envelope["data"]["checks"]}
    assert checks["wikis"]["ok"] is False
    assert "no wikis" in checks["wikis"]["message"]


def test_doctor_lore_root_missing_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LORE_ROOT", str(tmp_path / "does-not-exist"))
    monkeypatch.setenv("LORE_CACHE", str(tmp_path / "cache"))
    rc = doctor_cmd.main(["--cwd", str(tmp_path), "--json"])
    assert rc == 1
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["data"]["ok"] is False


def test_doctor_attach_check_finds_lore_block(healthy_vault, tmp_path, monkeypatch, capsys):
    """Registry-era: the `## Lore attach` check reads the synthesised
    block from the attachments registry (same message shape)."""
    from datetime import UTC, datetime
    from lore_core.state.attachments import Attachment, AttachmentsFile
    from lore_core.state.scopes import ScopesFile

    project = tmp_path / "proj"
    project.mkdir()
    af = AttachmentsFile(healthy_vault); af.load()
    af.add(Attachment(
        path=project, wiki="ccat", scope="ccat",
        attached_at=datetime.now(UTC), source="manual",
    ))
    af.save()
    sf = ScopesFile(healthy_vault); sf.load()
    sf.ingest_chain("ccat", "ccat")
    sf.save()

    rc = doctor_cmd.main(["--cwd", str(project), "--json"])
    assert rc == 0
    envelope = json.loads(capsys.readouterr().out)
    checks = {c["check"]: c for c in envelope["data"]["checks"]}
    assert "wiki=ccat" in checks["## Lore attach"]["message"]


def test_doctor_has_no_capture_pipeline_panel(healthy_vault, capsys):
    """Post-Task-12a: doctor is install-integrity only; capture activity
    moved to `lore status`. The 'Capture pipeline' header must be absent.
    """
    rc = doctor_cmd.main(["--cwd", str(healthy_vault)])
    out = capsys.readouterr().out
    assert "Capture pipeline" not in out
    assert rc == 0


def test_doctor_footer_points_to_status(healthy_vault, capsys):
    """Doctor's footer points the user at `lore status` for activity."""
    doctor_cmd.main(["--cwd", str(healthy_vault)])
    out = capsys.readouterr().out
    assert "lore status" in out


def test_doctor_capture_panel_lock_free_removed_smoke(tmp_path):
    """Placeholder so pytest collection still passes; old capture-panel
    tests for free-lock / hook-errors / marker are superseded by
    tests/test_capture_state.py (CaptureState field coverage).
    """
    events = tmp_path / ".lore" / "hook-events.jsonl"
    events.parent.mkdir(parents=True, exist_ok=True)
    events.write_text(
        json.dumps({
            "schema_version": 1,
            "ts": "2026-04-20T14:32:05Z",
            "event": "session-end",
            "outcome": "ledger-advanced",
        }) + "\n"
    )
    # Post-Task-12a: capture-pipeline details now live in CaptureState /
    # `lore status`; nothing to assert about the doctor panel here.
    # Kept as a smoke fixture so future test additions have a starting
    # point; delete the whole test if it starts rotting.
