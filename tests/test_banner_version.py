"""The banner version and the update nudge."""

from __future__ import annotations

from pathlib import Path

from lore_cli import hooks
from lore_core import source_root


def _plugin_dir(cache: Path, version: str) -> None:
    d = cache / version
    (d / "skills").mkdir(parents=True)
    (d / ".claude-plugin").mkdir()
    (d / ".claude-plugin" / "plugin.json").write_text(f'{{"version": "{version}"}}')


def test_newest_cached_plugin_sorts_by_number(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cache = tmp_path / ".claude" / "plugins" / "cache" / "lore" / "lore"
    for v in ("0.93.0", "0.99.1", "0.104.0"):
        _plugin_dir(cache, v)
    assert source_root._newest_cached_plugin() == cache / "0.104.0"


def _setup(monkeypatch, tmp_path, latest: str, installed: str) -> None:
    monkeypatch.delenv("LORE_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setenv("LORE_CACHE", str(tmp_path))
    (tmp_path / "latest-version").write_text(latest + "\n")
    monkeypatch.setattr("lore_core.session_start.lore_version", lambda: installed)


def test_update_hint_names_newer_release(tmp_path, monkeypatch):
    _setup(monkeypatch, tmp_path, "0.105.0", "0.99.1")
    hint = hooks._update_hint(spawn=False)
    assert hint is not None and "0.105.0" in hint and "lore update" in hint


def test_update_hint_silent_when_current(tmp_path, monkeypatch):
    _setup(monkeypatch, tmp_path, "0.104.0", "0.104.0")
    assert hooks._update_hint(spawn=False) is None


def test_update_hint_off_by_env(tmp_path, monkeypatch):
    _setup(monkeypatch, tmp_path, "0.105.0", "0.99.1")
    monkeypatch.setenv("LORE_NO_UPDATE_CHECK", "1")
    assert hooks._update_hint(spawn=False) is None


def test_update_hint_spawns_once_a_day(tmp_path, monkeypatch):
    _setup(monkeypatch, tmp_path, "0.104.0", "0.104.0")
    calls = []
    monkeypatch.setattr(hooks, "_spawn_in_repo", lambda *a: calls.append(a) or True)
    hooks._update_hint(spawn=True)
    hooks._update_hint(spawn=True)
    assert len(calls) == 1


def test_update_check_records_remote(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_CACHE", str(tmp_path))
    monkeypatch.setattr("lore_cli.integrations._fetch_remote_version", lambda: "0.105.0")
    hooks.cmd_update_check()
    assert (tmp_path / "latest-version").read_text().strip() == "0.105.0"
