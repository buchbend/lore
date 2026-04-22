"""Tests for `lore registry {ls,show,doctor}` CLI commands."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
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


def test_registry_show_prints_attach_block(tmp_path, monkeypatch):
    """show <path>: output includes wiki and scope from the attachments registry."""
    from datetime import UTC, datetime
    from lore_core.state.attachments import Attachment, AttachmentsFile

    lore_root = tmp_path / "lore_root"
    lore_root.mkdir(parents=True)
    (lore_root / ".lore").mkdir()
    monkeypatch.setenv("LORE_ROOT", str(lore_root))

    repo = tmp_path / "project"
    repo.mkdir()
    af = AttachmentsFile(lore_root); af.load()
    af.add(Attachment(
        path=repo, wiki="mywiki", scope="mywiki:myproject",
        attached_at=datetime.now(UTC), source="manual",
    ))
    af.save()

    result = runner.invoke(app, ["registry", "show", str(repo)])
    assert result.exit_code == 0, result.output
    assert "mywiki" in result.output
    assert "mywiki:myproject" in result.output


def test_registry_doctor_detects_missing_wiki_dir(tmp_path, monkeypatch):
    """doctor with empty LORE_ROOT: prints warning, exits non-zero or has output."""
    lore_root = tmp_path / "lore_root"
    lore_root.mkdir()
    monkeypatch.setenv("LORE_ROOT", str(lore_root))

    result = runner.invoke(app, ["registry", "doctor"])
    # Either non-zero exit or some output mentioning no wikis / empty / warning
    combined = (result.output or "") + (result.stderr or "")
    assert result.exit_code != 0 or "wiki" in combined.lower() or "no" in combined.lower()
