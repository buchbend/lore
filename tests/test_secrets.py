"""Secrets file and the secrets rule (PRD 0016, ADR 0016).

Config files name a secret; the value lives in ``~/.config/lore/secrets.yml``
(mode 0600, outside any git worktree).
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest
import yaml
from lore_cli.config_cmd import app
from lore_core.secrets import SecretsError, read_secrets, secrets_path
from typer.testing import CliRunner

runner = CliRunner()


def test_secret_set_writes_value_to_secrets_yml_with_mode_0600() -> None:
    result = runner.invoke(app, ["secret", "set", "PM_TOKEN"], input="s3cret\n")

    assert result.exit_code == 0, result.output
    path = secrets_path()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert yaml.safe_load(path.read_text()) == {"PM_TOKEN": "s3cret"}
    assert "s3cret" not in result.output


def test_secret_list_prints_names_only_and_unset_removes() -> None:
    runner.invoke(app, ["secret", "set", "A"], input="va\n")
    runner.invoke(app, ["secret", "set", "B"], input="vb\n")

    listed = runner.invoke(app, ["secret", "list"])
    assert listed.exit_code == 0
    assert "A" in listed.output and "B" in listed.output
    assert "va" not in listed.output and "vb" not in listed.output

    assert runner.invoke(app, ["secret", "unset", "A"]).exit_code == 0
    assert read_secrets() == {"B": "vb"}


def test_invariant_secrets_file_inside_a_git_worktree_is_refused(
    tmp_path: Path, monkeypatch
) -> None:
    dotfiles = tmp_path / "dotfiles"
    subprocess.run(["git", "init", "-q", str(dotfiles)], check=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(dotfiles))
    path = secrets_path()
    path.parent.mkdir(parents=True)
    path.write_text("PM_TOKEN: x\n")

    with pytest.raises(SecretsError, match=str(path)):
        read_secrets()

    result = runner.invoke(app, ["secret", "list"])
    assert result.exit_code != 0
    assert str(path) in result.output.replace("\n", "")
