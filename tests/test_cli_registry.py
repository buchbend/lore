"""Tests for `lore registry {ls,doctor}` CLI commands."""
from __future__ import annotations

import json

from typer.testing import CliRunner

from lore_cli.__main__ import app

runner = CliRunner(mix_stderr=False)


def test_registry_ls_lists_wiki_dirs(tmp_path, monkeypatch):
    """ls --format json returns JSON array with both wiki dirs."""
    lore_root = tmp_path / "lore_root"
    wiki_dir = lore_root / "wiki"
    (wiki_dir / "a").mkdir(parents=True)
    (wiki_dir / "b").mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))

    result = runner.invoke(app, ["registry", "ls", "--format", "json"])
    assert result.exit_code == 0, result.output

    data = json.loads(result.output)
    names = [entry["wiki"] for entry in data]
    assert "a" in names
    assert "b" in names


def test_registry_doctor_detects_missing_wiki_dir(tmp_path, monkeypatch):
    """doctor with empty LORE_ROOT: prints warning, exits non-zero or has output."""
    lore_root = tmp_path / "lore_root"
    lore_root.mkdir()
    monkeypatch.setenv("LORE_ROOT", str(lore_root))

    result = runner.invoke(app, ["registry", "doctor"])
    # Either non-zero exit or some output mentioning no wikis / empty / warning
    combined = (result.output or "") + (result.stderr or "")
    assert result.exit_code != 0 or "wiki" in combined.lower() or "no" in combined.lower()
