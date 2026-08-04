"""Tests for the packaged Vale style: resolution, `lore style vale-config`,
and the real-binary integration.

Vale is PATH-detected and not bundled with Lore (ADR 0006). The integration
tests below run the actual binary against a fixture with one banned word and
one 30-word sentence, and skip when Vale is absent — see PRD 0009's testing
decisions.
"""

from __future__ import annotations

import json
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
    glossary_terms,
    resolve_vale_config_path,
    vale_config_for,
)
from typer.testing import CliRunner

runner = CliRunner()

VALE_MISSING = shutil.which("vale") is None


@pytest.fixture(autouse=True)
def _cache_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every generated Vale config out of the developer's real cache dir."""
    monkeypatch.setenv("LORE_CACHE", str(tmp_path / "cache"))


@pytest.fixture()
def lore_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    (tmp_path / "wiki" / "notes").mkdir(parents=True)
    # `vale-config` reads the cwd's repo for a glossary. The checkout this
    # suite runs from holds one, so the CLI cases start from a directory that
    # does not — otherwise they compare against a generated copy.
    monkeypatch.chdir(tmp_path)
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


def test_packaged_vale_style_directory_is_the_writing_rules() -> None:
    """The ini and its rule directory are one unit — renaming one renames both."""
    config = default_vale_config_path()
    assert (config.parent / "WritingRules").is_dir()
    assert "BasedOnStyles = WritingRules" in config.read_text(encoding="utf-8")


# --- banned-word list stays single-sourced --------------------------------


def _listed_after(marker: str, text: str) -> list[str]:
    """The comma-separated words a `<marker>: a, b, c.` run names, unwrapped
    across line breaks."""
    match = re.search(rf"{re.escape(marker)}(.*?)\.", text, re.DOTALL)
    assert match, f"the writing rules lost the '{marker}' list"
    return [w.strip() for w in match.group(1).split(",")]


def _writing_rules_text() -> str:
    return default_style_path("writing-rules").read_text(encoding="utf-8")


def _vocabulary_tokens() -> list[str]:
    path = default_vale_config_path().parent / "WritingRules" / "Vocabulary.yml"
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


def test_vocabulary_rule_matches_the_banned_list() -> None:
    """Rule 3's words, the Vale tokens that enforce them, and the paste block's
    copy are three hand-synced lists — drift between them means the linter and
    the writing rules disagree about what is banned."""
    banned = _listed_after("Banned:", _writing_rules_text())
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


def test_paste_block_repeats_the_banned_list() -> None:
    text = _writing_rules_text()
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


# --- unknown short names: the glossary is the ignore list -----------------


def _repo_with_glossary(parent: Path, *terms: str) -> Path:
    """A repo directory whose CONTEXT.md defines ``terms`` in bold."""
    repo = parent / "repo"
    repo.mkdir(exist_ok=True)
    entries = "\n".join(f"- **{term}** — a defined thing." for term in terms)
    (repo / "CONTEXT.md").write_text(f"# Context\n\n## Language\n\n{entries}\n")
    return repo


def _vale(config: Path, fixture: Path) -> tuple[int, list[dict]]:
    """Run the real binary and return its exit code plus a flat alert list.

    JSON output rather than the console format: a long message wraps across
    console lines, so a substring assertion on the flagged word is unreliable.
    """
    result = subprocess.run(
        ["vale", "--output=JSON", "--config", str(config), str(fixture)],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 2, f"vale could not run:\n{result.stdout}{result.stderr}"
    by_file = json.loads(result.stdout or "{}")
    return result.returncode, [alert for alerts in by_file.values() for alert in alerts]


def test_packaged_ini_ships_the_short_name_check_switched_off() -> None:
    """The packaged config lints repos that have no glossary, so the check
    stays off until one switches it on. `vale_config_for` rewrites this exact
    line, so a config that drops it silently loses the check."""
    ini = default_vale_config_path().read_text(encoding="utf-8")
    assert "WritingRules.UnknownShortName = NO" in ini


def test_glossary_terms_takes_the_bold_terms(tmp_path: Path) -> None:
    repo = _repo_with_glossary(tmp_path, "L0", "Topic block", "C-ext")
    assert glossary_terms(repo) == ["L0", "Topic", "block", "C-ext"]


def test_glossary_terms_is_empty_without_a_context_md(tmp_path: Path) -> None:
    assert glossary_terms(tmp_path) == []


def test_config_for_a_repo_without_a_glossary_is_the_packaged_default(tmp_path: Path) -> None:
    assert vale_config_for(tmp_path) == default_vale_config_path()


def test_config_for_a_repo_with_a_glossary_switches_the_check_on(tmp_path: Path) -> None:
    repo = _repo_with_glossary(tmp_path, "L0")
    config = vale_config_for(repo)
    assert config != default_vale_config_path()
    assert "WritingRules.UnknownShortName = YES" in config.read_text(encoding="utf-8")
    assert (config.parent / "glossary.txt").read_text(encoding="utf-8").split() == ["L0"]
    # The whole rule directory travels, or the other rules stop firing.
    assert (config.parent / "WritingRules" / "Vocabulary.yml").is_file()


def test_the_generated_config_stays_out_of_the_repo(tmp_path: Path) -> None:
    """ADR 0006 keeps Lore from writing into a checkout it does not own."""
    repo = _repo_with_glossary(tmp_path, "L0")
    config = vale_config_for(repo)
    assert repo not in config.parents


def test_vale_config_cli_switches_the_check_on_inside_a_glossary_repo(
    lore_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_with_glossary(lore_root, "L0")
    monkeypatch.chdir(repo)
    result = runner.invoke(app, ["style", "vale-config"])
    assert result.exit_code == 0, result.output
    printed = Path(result.output.strip())
    assert "WritingRules.UnknownShortName = YES" in printed.read_text(encoding="utf-8")


@pytest.mark.skipif(VALE_MISSING, reason="vale not on PATH")
@pytest.mark.parametrize("name", ["G4", "LTA", "C-ext", "camelCase"])
def test_vale_flags_an_invented_short_name(tmp_path: Path, name: str) -> None:
    """Digit-bearing, uppercase, hyphenated and mixed-case tokens are the
    shapes Vale's default spelling filters skip. `custom: true` drops those
    filters, so all four reach the dictionary."""
    repo = _repo_with_glossary(tmp_path, "L0")
    fixture = tmp_path / "issue.md"
    fixture.write_text(f"# Title\n\nThe {name} loader reads the file.\n")
    code, alerts = _vale(vale_config_for(repo), fixture)
    assert name in [alert["Match"] for alert in alerts]
    assert code == 0, "an advisory finding must not read as a blocking one"


@pytest.mark.skipif(VALE_MISSING, reason="vale not on PATH")
def test_vale_leaves_a_glossary_short_name_alone(tmp_path: Path) -> None:
    repo = _repo_with_glossary(tmp_path, "L0")
    fixture = tmp_path / "issue.md"
    fixture.write_text("# Title\n\nThe L0 stage feeds the G4 loader.\n")
    _, alerts = _vale(vale_config_for(repo), fixture)
    matches = [alert["Match"] for alert in alerts]
    assert "G4" in matches, "the check did not run, so the L0 result proves nothing"
    assert "L0" not in matches


@pytest.mark.skipif(VALE_MISSING, reason="vale not on PATH")
def test_the_short_name_check_reports_at_warning(tmp_path: Path) -> None:
    repo = _repo_with_glossary(tmp_path, "L0")
    fixture = tmp_path / "issue.md"
    fixture.write_text("# Title\n\nThe G4 loader reads the file.\n")
    _, alerts = _vale(vale_config_for(repo), fixture)
    assert [alert["Severity"] for alert in alerts] == ["warning"]


@pytest.mark.skipif(VALE_MISSING, reason="vale not on PATH")
def test_a_banned_word_still_exits_1_beside_the_new_check(tmp_path: Path) -> None:
    """`file-issue` reads Vale by exit code: 1 is a blocking finding. The new
    warning must neither create one nor hide one."""
    repo = _repo_with_glossary(tmp_path, "L0")
    fixture = tmp_path / "issue.md"
    fixture.write_text("# Title\n\nWe should leverage the G4 loader.\n")
    code, alerts = _vale(vale_config_for(repo), fixture)
    assert code == 1
    assert {alert["Check"] for alert in alerts} == {
        "WritingRules.Vocabulary",
        "WritingRules.UnknownShortName",
    }


@pytest.mark.skipif(VALE_MISSING, reason="vale not on PATH")
def test_a_repo_without_a_glossary_runs_no_short_name_check(tmp_path: Path) -> None:
    """With no glossary the check would flag every domain word, so it is off."""
    fixture = tmp_path / "issue.md"
    fixture.write_text("# Title\n\nThe G4 loader reads the file.\n")
    code, alerts = _vale(vale_config_for(tmp_path), fixture)
    assert alerts == []
    assert code == 0


@pytest.mark.skipif(VALE_MISSING, reason="vale not on PATH")
@pytest.mark.parametrize("token", ["buchbend/lore#336", "buchbend/lore", "lore_core/style.py"])
def test_the_check_passes_over_a_path_or_a_repo_slug(tmp_path: Path, token: str) -> None:
    """A token carrying a slash is a path, a repo name or an issue reference.
    Issue bodies are full of them and none is a short name for a thing."""
    repo = _repo_with_glossary(tmp_path, "L0")
    fixture = tmp_path / "issue.md"
    fixture.write_text(f"# Title\n\nThe change lands in {token} today.\n")
    _, alerts = _vale(vale_config_for(repo), fixture)
    assert alerts == []


@pytest.mark.skipif(VALE_MISSING, reason="vale not on PATH")
@pytest.mark.parametrize("token", ["banned-word", "mixed-case", "generation-time", "paste-block"])
def test_the_check_passes_over_an_english_compound(tmp_path: Path, token: str) -> None:
    """Vale reads a hyphenated compound as one token, and no dictionary holds
    the compound. Every segment being an English word of three letters or more
    marks the compound as prose rather than a name."""
    repo = _repo_with_glossary(tmp_path, "L0")
    fixture = tmp_path / "issue.md"
    fixture.write_text(f"# Title\n\nThe {token} rule reads the file.\n")
    _, alerts = _vale(vale_config_for(repo), fixture)
    assert alerts == []


def test_an_unwritable_cache_falls_back_to_the_plain_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0006 keeps the lint from blocking the flow. A cache Lore cannot
    write costs the short-name check, not the whole `vale --config` call."""
    repo = _repo_with_glossary(tmp_path, "L0")
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    monkeypatch.setenv("LORE_CACHE", str(blocker))
    assert vale_config_for(repo) == default_vale_config_path()


@pytest.mark.skipif(VALE_MISSING, reason="vale not on PATH")
@pytest.mark.parametrize("token", ["another's", "repo's", "cwd's"])
def test_the_check_passes_over_a_possessive(tmp_path: Path, token: str) -> None:
    """Only the trailing `'s` makes `another's` unknown. Vale filters
    possessives by default, and `custom: true` drops that filter with the
    rest, so the rule restores it."""
    repo = _repo_with_glossary(tmp_path, "L0")
    fixture = tmp_path / "issue.md"
    fixture.write_text(f"# Title\n\nThe check reads {token} first line.\n")
    _, alerts = _vale(vale_config_for(repo), fixture)
    assert [alert["Match"] for alert in alerts] == []


@pytest.mark.skipif(VALE_MISSING, reason="vale not on PATH")
def test_the_check_passes_over_a_fragment_left_by_a_code_span(tmp_path: Path) -> None:
    """Vale strips the code span from ``an `error`-level alert`` and leaves
    `-level` behind. A token opening with a hyphen is that leftover."""
    repo = _repo_with_glossary(tmp_path, "L0")
    fixture = tmp_path / "issue.md"
    fixture.write_text("# Title\n\nVale reports an `error`-level alert here.\n")
    _, alerts = _vale(vale_config_for(repo), fixture)
    assert [alert["Match"] for alert in alerts] == []
