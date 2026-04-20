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


def test_doctor_capture_panel_empty(tmp_path):
    from lore_cli.doctor_cmd import run_capture_panel

    lines = run_capture_panel(tmp_path)
    assert any("no capture activity" in l.lower() for l in lines)


def test_doctor_capture_panel_last_hook_and_run_and_note(tmp_path):
    from lore_cli.doctor_cmd import run_capture_panel

    events = tmp_path / ".lore" / "hook-events.jsonl"
    runs = tmp_path / ".lore" / "runs"
    events.parent.mkdir(parents=True)
    runs.mkdir()
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    events.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "ts": now,
                "event": "session-end",
                "outcome": "spawned-curator",
                "duration_ms": 40,
            }
        )
        + "\n"
    )
    (runs / "2026-04-20T14-32-05-aaaaaa.jsonl").write_text(
        json.dumps(
            {"type": "run-start", "ts": now, "trigger": "hook"}
        )
        + "\n"
        + json.dumps(
            {
                "type": "session-note",
                "ts": now,
                "action": "filed",
                "wikilink": "[[some-note]]",
            }
        )
        + "\n"
        + json.dumps(
            {
                "type": "run-end",
                "ts": now,
                "duration_ms": 3000,
                "notes_new": 1,
                "notes_merged": 0,
                "skipped": 0,
                "errors": 0,
            }
        )
        + "\n"
    )
    lines = run_capture_panel(tmp_path)
    flat = " ".join(lines)
    assert "Last hook fired" in flat
    assert "Last curator run" in flat
    assert "Last note filed" in flat
    assert "some-note" in flat


def test_doctor_capture_panel_lock_holder(tmp_path):
    from lore_core.lockfile import curator_lock
    with curator_lock(tmp_path, timeout=0.0, run_id="r-abc123"):
        from lore_cli.doctor_cmd import run_capture_panel
        lines = run_capture_panel(tmp_path)
    flat = " ".join(lines)
    assert "Curator lock held by PID" in flat
    assert "r-abc123" in flat


def test_doctor_capture_panel_lock_free(tmp_path):
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
    from lore_cli.doctor_cmd import run_capture_panel
    lines = run_capture_panel(tmp_path)
    flat = " ".join(lines)
    assert "Curator lock free" in flat


def test_doctor_capture_panel_hook_error_warning(tmp_path):
    from lore_cli.doctor_cmd import run_capture_panel

    events = tmp_path / ".lore" / "hook-events.jsonl"
    events.parent.mkdir(parents=True)
    ts = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    events.write_text(
        json.dumps(
            {"schema_version": 1, "ts": ts, "event": "session-end", "outcome": "error"}
        )
        + "\n"
    )
    lines = run_capture_panel(tmp_path)
    flat = " ".join(lines)
    assert "hook error" in flat.lower()
