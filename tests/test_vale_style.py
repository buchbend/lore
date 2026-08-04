"""Tests for the packaged Vale style: resolution, `lore style vale-config`,
and the real-binary integration.

Vale is PATH-detected and not bundled with Lore (ADR 0006). The integration
tests below run the actual binary against a fixture with one banned word and
one 30-word sentence, and skip when Vale is absent — see PRD 0009's testing
decisions.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from lore_cli.__main__ import app
from lore_core.style import (
    default_style_path,
    default_vale_config_path,
    resolve_vale_config_path,
)
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


# --- banned-word list stays single-sourced --------------------------------


def _listed_after(marker: str, text: str) -> list[str]:
    """The comma-separated words a `<marker>: a, b, c.` run names, unwrapped
    across line breaks."""
    match = re.search(rf"{re.escape(marker)}(.*?)\.", text, re.DOTALL)
    assert match, f"the register lost its '{marker}' list"
    return [w.strip() for w in match.group(1).split(",")]


def _register_text() -> str:
    return default_style_path("issue-register").read_text(encoding="utf-8")


def _vocabulary_tokens() -> list[str]:
    path = default_vale_config_path().parent / "IssueRegister" / "Vocabulary.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))["tokens"]


def _vocabulary_bases() -> list[str]:
    """The plain word each token is built around.

    A token carries the word's inflections, so the base is the leading run of
    letters: `leverage(?:s|d|ly)?|leveraging` is built around `leverage`.
    """
    bases = []
    for token in _vocabulary_tokens():
        match = re.match(r"[a-z]+", token)
        assert match, f"token {token!r} does not start with a plain word"
        bases.append(match.group(0))
    return bases


def test_vocabulary_rule_matches_the_register_banned_list() -> None:
    """Rule 3's words, the Vale tokens that enforce them, and the paste block's
    copy are three hand-synced lists — drift between them means the linter and
    the register disagree about what is banned."""
    banned = _listed_after("Banned:", _register_text())
    assert _vocabulary_bases() == banned


def test_vocabulary_tokens_carry_inflections() -> None:
    """A token that is only the base word lets the inflected forms through.

    Vale wraps each token in word boundaries, so a bare `leverage` matches that
    spelling alone and "leverages" passes the lint.
    """
    bare = [t for t in _vocabulary_tokens() if re.fullmatch(r"[a-z]+", t)]
    assert not bare, (
        f"these tokens match the base form only, so their plurals and "
        f"participles pass the lint: {bare!r}"
    )


def test_paste_block_repeats_the_register_banned_list() -> None:
    text = _register_text()
    paste = text.split("## Block for CLAUDE.md and AGENTS.md", 1)[1]
    assert _listed_after("Do not use:", paste) == _listed_after("Banned:", text)


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
@pytest.mark.parametrize("word", ["leverages", "leveraging", "streamlining", "underscored"])
def test_vale_flags_an_inflected_banned_word(tmp_path: Path, word: str) -> None:
    """The plural and participle forms are the ones writers reach for."""
    fixture = tmp_path / "issue.md"
    fixture.write_text(f"# Title\n\nThe service {word} the existing path.\n")
    result = subprocess.run(
        ["vale", "--config", str(default_vale_config_path()), str(fixture)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, f"{word!r} passed the lint:\n{result.stdout}"


@pytest.mark.skipif(VALE_MISSING, reason="vale not on PATH")
@pytest.mark.parametrize("word", ["elevator", "elevation"])
def test_vale_leaves_unrelated_words_alone(tmp_path: Path, word: str) -> None:
    """A stem match would flag these; rule 3 bans "elevate", not its cousins."""
    fixture = tmp_path / "issue.md"
    fixture.write_text(f"# Title\n\nThe {word} is out of scope here.\n")
    result = subprocess.run(
        ["vale", "--config", str(default_vale_config_path()), str(fixture)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"{word!r} was flagged:\n{result.stdout}"


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
