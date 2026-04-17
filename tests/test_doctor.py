"""Tests for `lore doctor`."""

from __future__ import annotations

import json
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
    project = tmp_path / "proj"
    project.mkdir()
    (project / "CLAUDE.md").write_text(
        "# proj\n\n## Lore\n- wiki: ccat\n- scope: ccat\n"
    )
    rc = doctor_cmd.main(["--cwd", str(project), "--json"])
    assert rc == 0
    envelope = json.loads(capsys.readouterr().out)
    checks = {c["check"]: c for c in envelope["data"]["checks"]}
    assert "wiki=ccat" in checks["## Lore attach"]["message"]
