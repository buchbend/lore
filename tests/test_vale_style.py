"""Tests for the packaged Vale style: resolution, `lore style vale-config`,
and the real-binary integration.

Vale is PATH-detected and not bundled with Lore (ADR 0006). The integration
tests below run the actual binary against a fixture with one banned word and
one 30-word sentence, and skip when Vale is absent — see PRD 0009's testing
decisions.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from lore_cli.__main__ import app
from lore_core.style import default_vale_config_path, resolve_vale_config_path
from typer.testing import CliRunner

runner = CliRunner()

VALE_MISSING = shutil.which("vale") is None


@pytest.fixture()
def lore_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    (tmp_path / "wiki" / "notes").mkdir(parents=True)
    return tmp_path


# --- resolution ----------------------------------------------------------


def test_default_vale_config_path_is_a_packaged_ini() -> None:
    path = default_vale_config_path()
    assert path.is_file()
    assert path.name == "vale.ini"


def test_resolve_falls_back_to_packaged_default(tmp_path: Path) -> None:
    assert resolve_vale_config_path(wiki_dir=tmp_path) == default_vale_config_path()


def test_wiki_vale_override_wins(tmp_path: Path) -> None:
    override = tmp_path / "style" / "vale" / "vale.ini"
    override.parent.mkdir(parents=True)
    override.write_text("StylesPath = .\n")
    assert resolve_vale_config_path(wiki_dir=tmp_path) == override


def test_resolve_with_no_wiki_dir_returns_packaged_default() -> None:
    assert resolve_vale_config_path(wiki_dir=None) == default_vale_config_path()


# --- CLI -------------------------------------------------------------------


def test_vale_config_cli_prints_the_packaged_default_path(lore_root: Path) -> None:
    result = runner.invoke(app, ["style", "vale-config"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == str(default_vale_config_path())


def test_vale_config_cli_prints_the_wiki_override_path(lore_root: Path) -> None:
    override = lore_root / "wiki" / "notes" / "style" / "vale" / "vale.ini"
    override.parent.mkdir(parents=True)
    override.write_text("StylesPath = .\n")
    result = runner.invoke(app, ["style", "vale-config", "--wiki", "notes"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == str(override)


def test_vale_config_cli_falls_back_when_wiki_has_no_override(lore_root: Path) -> None:
    result = runner.invoke(app, ["style", "vale-config", "--wiki", "notes"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == str(default_vale_config_path())


# --- real-binary integration (skipped when Vale is not on PATH) -----------


@pytest.mark.skipif(VALE_MISSING, reason="vale not on PATH")
def test_vale_flags_a_banned_word(tmp_path: Path) -> None:
    fixture = tmp_path / "issue.md"
    fixture.write_text("# Title\n\nWe should leverage the existing system.\n")
    result = subprocess.run(
        ["vale", "--config", str(default_vale_config_path()), str(fixture)],
        capture_output=True,
        text=True,
    )
    assert "leverage" in result.stdout.lower()


@pytest.mark.skipif(VALE_MISSING, reason="vale not on PATH")
def test_vale_flags_a_sentence_over_25_words(tmp_path: Path) -> None:
    fixture = tmp_path / "issue.md"
    long_sentence = " ".join(["word"] * 30) + ".\n"
    fixture.write_text(f"# Title\n\n{long_sentence}")
    result = subprocess.run(
        ["vale", "--config", str(default_vale_config_path()), str(fixture)],
        capture_output=True,
        text=True,
    )
    assert "sentence" in result.stdout.lower()
    assert result.returncode != 0
