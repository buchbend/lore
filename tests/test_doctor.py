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


# ----- LORE_ROOT source label (issue #6) -----


def test_doctor_reports_source_env(healthy_vault, capsys):
    """When $LORE_ROOT is set, doctor labels the source ``$LORE_ROOT``."""
    rc = doctor_cmd.main(["--cwd", str(healthy_vault), "--json"])
    assert rc == 0
    envelope = json.loads(capsys.readouterr().out)
    msg = next(c for c in envelope["data"]["checks"] if c["check"] == "LORE_ROOT")["message"]
    assert "[$LORE_ROOT]" in msg


def test_doctor_reports_source_config(tmp_path, monkeypatch, capsys):
    """When config-file is set (env unset), doctor labels source ``config-file``."""
    vault = tmp_path / "vault"
    (vault / "wiki" / "ccat").mkdir(parents=True)
    cfg_dir = Path.home() / ".config" / "lore"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.yml").write_text(f"lore_root: {vault}\n")
    monkeypatch.delenv("LORE_ROOT", raising=False)
    monkeypatch.setenv("LORE_CACHE", str(tmp_path / "cache"))
    rc = doctor_cmd.main(["--cwd", str(vault), "--json"])
    assert rc == 0
    envelope = json.loads(capsys.readouterr().out)
    msg = next(c for c in envelope["data"]["checks"] if c["check"] == "LORE_ROOT")["message"]
    assert "[config-file]" in msg


def test_doctor_reports_source_default_when_unconfigured(tmp_path, monkeypatch, capsys):
    """When neither env nor config-file is set, doctor labels source ``unconfigured (fallback)``."""
    monkeypatch.delenv("LORE_ROOT", raising=False)
    monkeypatch.setenv("LORE_CACHE", str(tmp_path / "cache"))
    # Create the default ~/lore so the check itself passes existence,
    # which lets us verify the label without conflating with the missing
    # path failure mode.
    (Path.home() / "lore" / "wiki").mkdir(parents=True)
    rc = doctor_cmd.main(["--cwd", str(Path.home() / "lore"), "--json"])
    envelope = json.loads(capsys.readouterr().out)
    msg = next(c for c in envelope["data"]["checks"] if c["check"] == "LORE_ROOT")["message"]
    assert "[unconfigured (fallback)]" in msg


def test_doctor_reports_source_even_on_missing_root(tmp_path, monkeypatch, capsys):
    """Missing path failure case still includes the source label."""
    ghost = tmp_path / "ghost-vault"
    monkeypatch.setenv("LORE_ROOT", str(ghost))
    monkeypatch.setenv("LORE_CACHE", str(tmp_path / "cache"))
    rc = doctor_cmd.main(["--cwd", str(tmp_path), "--json"])
    assert rc == 1
    envelope = json.loads(capsys.readouterr().out)
    msg = next(c for c in envelope["data"]["checks"] if c["check"] == "LORE_ROOT")["message"]
    assert "[$LORE_ROOT]" in msg
    assert "does not exist" in msg


def test_doctor_attach_check_finds_lore_block(healthy_vault, tmp_path, monkeypatch, capsys):
    """The `attach` check reads the synthesised block from the
    attachments registry."""
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
    assert "wiki=ccat" in checks["attach"]["message"]


def test_doctor_has_no_capture_pipeline_panel(healthy_vault, capsys):
    """Doctor is install-integrity only; capture activity lives in
    `lore status`. The 'Capture pipeline' header must be absent.
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


# ---------------------------------------------------------------------------
# `_check_claude_plugin_cache_drift` — Claude plugin cache vs pip drift
# ---------------------------------------------------------------------------


def _write_plugin_index(home: Path, *, version: str | None, scope: str = "user") -> None:
    """Write a minimal `~/.claude/plugins/installed_plugins.json`."""
    plugins_dir = home / ".claude" / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    if version is not None:
        entries.append(
            {"scope": scope, "version": version, "installPath": str(plugins_dir / "lore" / version)}
        )
    (plugins_dir / "installed_plugins.json").write_text(
        json.dumps({"version": 2, "plugins": {"lore@lore": entries}})
    )


def test_plugin_cache_drift_skips_when_no_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor_cmd.Path, "home", classmethod(lambda cls: tmp_path))
    ok, msg = doctor_cmd._check_claude_plugin_cache_drift(str(tmp_path))
    assert ok is True
    assert "no Claude plugin cache" in msg


def test_plugin_cache_drift_skips_when_lore_not_installed_in_claude(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor_cmd.Path, "home", classmethod(lambda cls: tmp_path))
    _write_plugin_index(tmp_path, version=None)
    ok, msg = doctor_cmd._check_claude_plugin_cache_drift(str(tmp_path))
    assert ok is True
    assert "not present" in msg


def test_plugin_cache_drift_passes_when_versions_match(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor_cmd.Path, "home", classmethod(lambda cls: tmp_path))
    _write_plugin_index(tmp_path, version="0.13.1")
    # Stub importlib.metadata.version to return the matching string.
    import importlib.metadata as _md
    monkeypatch.setattr(_md, "version", lambda _name: "0.13.1")
    ok, msg = doctor_cmd._check_claude_plugin_cache_drift(str(tmp_path))
    assert ok is True
    assert "0.13.1 matches pip" in msg


def test_plugin_cache_drift_fails_when_pip_lags_behind_cache(tmp_path, monkeypatch):
    """The headline footgun: `claude plugin update` advanced the cache,
    pipx wasn't upgraded, banner silently shows the old version."""
    monkeypatch.setattr(doctor_cmd.Path, "home", classmethod(lambda cls: tmp_path))
    _write_plugin_index(tmp_path, version="0.13.1")
    import importlib.metadata as _md
    monkeypatch.setattr(_md, "version", lambda _name: "0.10.0")
    ok, msg = doctor_cmd._check_claude_plugin_cache_drift(str(tmp_path))
    assert ok is False
    assert "0.13.1" in msg
    assert "0.10.0" in msg
    assert "pipx upgrade lore" in msg
    assert "restart Claude Code" in msg


def test_plugin_cache_drift_unreadable_index_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor_cmd.Path, "home", classmethod(lambda cls: tmp_path))
    plugins_dir = tmp_path / ".claude" / "plugins"
    plugins_dir.mkdir(parents=True)
    (plugins_dir / "installed_plugins.json").write_text("{not json")
    ok, msg = doctor_cmd._check_claude_plugin_cache_drift(str(tmp_path))
    assert ok is False
    assert "unreadable" in msg


def test_plugin_cache_drift_fails_the_overall_run(healthy_vault, tmp_path, monkeypatch, capsys):
    """Plugin-cache drift is a *failing* check, not advisory:
    `lore doctor`'s overall exit code must go non-zero on drift, unlike the
    old advisory behaviour."""
    monkeypatch.setattr(doctor_cmd.Path, "home", classmethod(lambda cls: tmp_path))
    _write_plugin_index(tmp_path, version="0.13.1")
    import importlib.metadata as _md
    monkeypatch.setattr(_md, "version", lambda _name: "0.10.0")

    rc = doctor_cmd.main(["--cwd", str(healthy_vault), "--json"])
    assert rc == 1
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["data"]["ok"] is False
    checks = {c["check"]: c for c in envelope["data"]["checks"]}
    assert checks["plugin cache"]["ok"] is False


def test_doctor_capture_panel_lock_free_removed_smoke(tmp_path):
    """Placeholder so pytest collection still passes; old capture-panel
    tests for free-lock / hook-errors / marker are superseded by
    tests/test_capture_state.py (CaptureState field coverage).
    """
    events = tmp_path / ".lore" / "spine.jsonl"
    events.parent.mkdir(parents=True, exist_ok=True)
    events.write_text(
        json.dumps({
            "ts": "2026-04-20T14:32:05Z", "v": 1, "source": "hook",
            "event": "session-end", "level": "info", "trace_id": None,
            "session_id": None, "run_id": None, "wiki": None, "scope": None,
            "error_code": None, "data": {"outcome": "ledger-advanced"},
        }) + "\n"
    )
    # Post-Task-12a: capture-pipeline details now live in CaptureState /
    # `lore status`; nothing to assert about the doctor panel here.
    # Kept as a smoke fixture so future test additions have a starting
    # point; delete the whole test if it starts rotting.


def test_check_spine_writable_reports_degrade_marker(tmp_path, monkeypatch):
    """AC4: a spine write-failure degrade marker is surfaced by doctor."""
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    (tmp_path / ".lore").mkdir(parents=True, exist_ok=True)

    ok, msg = doctor_cmd._check_spine_writable(str(tmp_path))
    assert ok is True
    assert "spine" in msg.lower()

    (tmp_path / ".lore" / "spine-failed.marker").touch()
    ok, msg = doctor_cmd._check_spine_writable(str(tmp_path))
    assert ok is False
    assert "spine write failed" in msg.lower()
