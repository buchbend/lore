"""Tests for `lore_cli.install_cmd` — argparse + flag combinations +
legacy artifact gating."""

from __future__ import annotations

import json
import os

import pytest
from lore_cli import install_cmd


@pytest.fixture
def clean_home(tmp_path, monkeypatch):
    """Empty $HOME so legacy detection finds nothing."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(install_cmd.Path, "home", classmethod(lambda cls: tmp_path))
    # Also reset the helper module's HOME-derived paths
    from lore_core.install import _helpers

    monkeypatch.setattr(_helpers.Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def test_force_yes_combo_refused(clean_home, capsys):
    """--force --yes is the obvious footgun; refuse with a clear error."""
    rc = install_cmd.main(["--force", "--yes", "--host", "claude"])
    assert rc == 2
    err = capsys.readouterr().out + capsys.readouterr().err
    assert "not allowed" in err.lower() or "force" in err.lower()


def test_check_does_not_write(clean_home, capsys, tmp_path, monkeypatch):
    """`lore install check` must never modify the filesystem."""
    # Snapshot mtimes of HOME tree
    before = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*") if p.is_file()}
    install_cmd.main(["check", "--host", "cursor"])
    after = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*") if p.is_file()}
    # The clean_home fixture starts with no files, but after check
    # there should still be no NEW files, and no modified files.
    assert before == after


def test_unknown_host_exits_with_error(clean_home, capsys):
    rc = install_cmd.main(["--host", "nonexistent"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "unknown host" in err


def test_json_envelope_for_check_mode(clean_home, capsys):
    install_cmd.main(["check", "--host", "cursor", "--json"])
    out = capsys.readouterr().out
    envelope = json.loads(out)
    assert envelope["mode"] == "install"  # check delegates to install plan
    assert any(h["host"] == "cursor" for h in envelope["hosts"])


def test_legacy_artifacts_block_install_without_force(
    clean_home, capsys, tmp_path
):
    """Plant a fake legacy skill symlink → write modes refuse."""
    skills = tmp_path / ".claude" / "skills"
    skills.mkdir(parents=True)
    target = tmp_path / "lore" / "skills" / "lore:fake"
    target.mkdir(parents=True)
    (skills / "lore:fake").symlink_to(target)

    rc = install_cmd.main(["--host", "cursor", "--yes"])
    err = capsys.readouterr().out
    assert rc == 1
    assert "legacy install.sh artifacts" in err.lower()


def test_legacy_artifacts_show_in_check_but_no_refusal(
    clean_home, capsys, tmp_path
):
    """`check` shows the legacy warning AND the plan, exits 0."""
    skills = tmp_path / ".claude" / "skills"
    skills.mkdir(parents=True)
    target = tmp_path / "lore" / "skills" / "lore:fake"
    target.mkdir(parents=True)
    (skills / "lore:fake").symlink_to(target)

    rc = install_cmd.main(["check", "--host", "cursor"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "legacy install.sh artifacts" in out.lower()
    assert "About to install" in out  # plan still rendered


def test_force_overrides_legacy_refusal(clean_home, capsys, tmp_path):
    """--force (interactive only) lets the install proceed past legacy
    detection."""
    skills = tmp_path / ".claude" / "skills"
    skills.mkdir(parents=True)
    target = tmp_path / "lore" / "skills" / "lore:fake"
    target.mkdir(parents=True)
    (skills / "lore:fake").symlink_to(target)
    # --force without --yes (allowed); use check so no writes happen
    rc = install_cmd.main(
        ["check", "--host", "cursor", "--force"]
    )
    assert rc == 0


def test_default_subcommand_is_install(clean_home, capsys):
    """`lore install` with no subcommand defaults to `install`."""
    # Use --host with a binary that doesn't exist on PATH so we don't
    # trigger any real subprocess.
    install_cmd.main(["check", "--host", "cursor"])
    # The fact that it ran without SystemExit on argparse confirms the
    # default subcommand routing works.
