"""`lore wiki check` replaces `lore wiki lint`; `lore adr check` gets its own name (issue #600)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from lore_cli.__main__ import app
from typer.testing import CliRunner

REPO = Path(__file__).resolve().parent.parent
VALE_MISSING = shutil.which("vale") is None
runner = CliRunner()

GOOD = "---\ndescription: A short note.\n---\nA short body.\n"


@pytest.fixture
def wiki(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    w = tmp_path / "wiki" / "demo"
    w.mkdir(parents=True)
    return w


def _note(wiki: Path, rel: str, text: str = GOOD) -> Path:
    path = wiki / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _check(*args: str) -> tuple[int, list[dict]]:
    result = runner.invoke(app, ["wiki", "check", "--json", *args])
    return result.exit_code, json.loads(result.stdout)["findings"]


def _structure(findings: list[dict]) -> list[tuple[str, str]]:
    return sorted((f["file"], f["check"]) for f in findings if f["check"] != "vale")


# --- 1. Lore generates no derived wiki files --------------------------------


def test_lint_command_and_module_are_gone():
    result = runner.invoke(app, ["wiki", "lint"])
    assert result.exit_code == 2
    assert "No such command" in result.output
    assert not (REPO / "lib" / "lore_core" / "lint.py").exists()
    assert not (REPO / "examples" / "wiki-lint.yml").exists()


def test_scaffold_and_check_write_no_derived_file(wiki, monkeypatch):
    from lore_cli.wiki_cmd import scaffold_wiki

    scaffold_wiki("fresh")
    _note(wiki, "concepts/a.md")
    assert _check()[0] == 0
    for root in (wiki, wiki.parent / "fresh"):
        for name in ("_catalog.json", "_index.txt", "_concepts.txt", "_decisions.txt"):
            assert not (root / name).exists()


def test_banner_shows_focus_block_without_a_catalog(wiki):
    from lore_core.session_start import collect_session_facts, render_session_banner

    _note(wiki, "projects/lore/lore.md", "---\ndescription: The Lore project.\n---\nbody\n")
    banner = render_session_banner(collect_session_facts(wiki, "buchbend/lore"))
    assert "## Focus: [[lore]]" in banner
    assert "The Lore project." in banner


def test_project_note_matches_the_repos_field_first(wiki):
    from lore_core.session_start import project_note_for_repo

    _note(wiki, "projects/lore.md")
    _note(wiki, "projects/other.md", "---\ndescription: d\nrepos: [buchbend/lore]\n---\n")
    assert project_note_for_repo(wiki, "buchbend/lore")["name"] == "other"
    assert project_note_for_repo(wiki, "buchbend/none") is None


def test_project_note_ignores_a_substring_of_a_scalar_repos_field(wiki):
    from lore_core.session_start import project_note_for_repo

    _note(wiki, "projects/plugin.md", "---\ndescription: d\nrepos: buchbend/lore-plugin\n---\n")
    _note(wiki, "projects/README.md")
    _note(wiki, "projects/odd.md", "---\ndescription: d\nrepos: 42\n---\n")
    assert project_note_for_repo(wiki, "buchbend/lore") is None
    assert project_note_for_repo(wiki, "buchbend/lore-plugin")["name"] == "plugin"
    assert project_note_for_repo(wiki, "buchbend/readme") is None


def test_mcp_resolves_a_slug_without_a_catalog(wiki):
    from lore_mcp.server import _resolve_slug, handle_read

    _note(wiki, "observatory/telescope.md", GOOD)
    assert _resolve_slug(wiki, "telescope") == "observatory/telescope.md"
    assert _resolve_slug(wiki, "missing") is None
    assert "A short body." in handle_read("[[telescope]]", wiki="demo")["content"]


def test_slug_lookup_prefers_a_checked_note_over_the_inbox(wiki):
    from lore_mcp.server import _resolve_slug

    _note(wiki, "inbox/foo.md")
    _note(wiki, "inbox/draft.md")
    _note(wiki, "projects/foo.md")
    _note(wiki, ".obsidian/hidden.md")
    assert _resolve_slug(wiki, "foo") == "projects/foo.md"
    assert _resolve_slug(wiki, "draft") == "inbox/draft.md"
    assert _resolve_slug(wiki, "hidden") is None


# --- 2. One check command for wiki notes ------------------------------------


def test_check_covers_every_folder_outside_the_skip_list(wiki):
    bare = "no frontmatter\n"
    _note(wiki, "observatory/deep/a.md", bare)
    _note(wiki, "root-note.md", bare)
    for skipped in ("inbox/x.md", "templates/t.md", ".obsidian/o.md", "README.md", "CLAUDE.md"):
        _note(wiki, skipped, bare)
    code, findings = _check()
    assert code == 1
    assert _structure(findings) == [
        ("observatory/deep/a.md", "missing_description"),
        ("root-note.md", "missing_description"),
    ]
    assert all(f["severity"] == "error" for f in findings if f["check"] != "vale")


def test_broken_link_is_an_error_and_resolves_in_the_same_wiki_only(wiki):
    _note(wiki, "concepts/target.md")
    _note(wiki, "concepts/renamed.md", "---\ndescription: d\naliases: [old-name]\n---\n")
    _note(wiki.parent / "other", "concepts/elsewhere.md")
    _note(
        wiki,
        "concepts/a.md",
        "---\ndescription: d\n---\n"
        "See [[target]], [[target#Part|shown]], [[old-name]].\n"
        "Code stays literal: `[[in-code]]`.\n```\n[[in-fence]]\n```\n"
        "Broken: [[ghost]] and [[elsewhere]].\n",
    )
    code, findings = _check("--wiki", "demo")
    assert code == 1
    broken = [f["message"] for f in findings if f["check"] == "broken_link"]
    assert len(broken) == 2
    assert "[[ghost]]" in broken[0] and "[[elsewhere]]" in broken[1]


def test_a_link_to_an_attachment_resolves(wiki, monkeypatch):
    monkeypatch.setattr("lore_core.wiki_check.shutil.which", lambda _: None)
    (wiki / "concepts").mkdir()
    (wiki / "concepts" / "arch.png").write_bytes(b"png")
    _note(wiki, "concepts/a.md", "---\ndescription: d\n---\n![[arch.png]] ![[gone.png]]\n")
    code, findings = _check()
    assert code == 1
    assert [f["message"] for f in findings] == ["[[gone.png]] has no target in this wiki"]


def test_a_dangling_symlink_does_not_stop_the_check(wiki, monkeypatch):
    monkeypatch.setattr("lore_core.wiki_check.shutil.which", lambda _: None)
    _note(wiki, "concepts/a.md")
    (wiki / "concepts" / "dead.md").symlink_to(wiki / "nowhere.md")
    assert _check() == (0, [])


def test_empty_description_is_an_error(wiki):
    _note(wiki, "concepts/a.md", "---\ndescription: ''\n---\nbody\n")
    code, findings = _check()
    assert code == 1
    assert _structure(findings) == [("concepts/a.md", "missing_description")]


def test_two_notes_with_one_filename_are_an_error(wiki):
    _note(wiki, "concepts/same.md")
    _note(wiki, "systems/same.md")
    code, findings = _check()
    assert code == 1
    assert _structure(findings) == [
        ("concepts/same.md", "duplicate_filename"),
        ("systems/same.md", "duplicate_filename"),
    ]


def test_size_folder_and_link_count_get_no_finding(wiki, monkeypatch):
    monkeypatch.setattr("lore_core.wiki_check.shutil.which", lambda _: None)
    # 400 lines, an unknown folder with no index note, no link in or out.
    _note(wiki, "anything/goes/big.md", "---\ndescription: d\n---\n" + "A line.\n" * 400)
    code, findings = _check()
    assert (code, findings) == (0, [])


def test_file_path_checks_that_file_alone(wiki):
    _note(wiki, "concepts/bad.md", "no frontmatter\n")
    one = _note(wiki, "concepts/one.md", "---\ndescription: d\n---\n[[ghost]] [[bad]]\n")
    code, findings = _check(str(one))
    assert code == 1
    assert _structure(findings) == [("concepts/one.md", "broken_link")]


def test_file_outside_a_wiki_is_a_usage_error(wiki, tmp_path):
    stray = tmp_path / "stray.md"
    stray.write_text(GOOD)
    assert runner.invoke(app, ["wiki", "check", str(stray)]).exit_code == 2


def test_vale_absence_is_named_in_one_line(wiki, monkeypatch):
    monkeypatch.setattr("lore_core.wiki_check.shutil.which", lambda _: None)
    _note(wiki, "concepts/a.md")
    result = runner.invoke(app, ["wiki", "check"])
    assert result.exit_code == 0
    assert [line for line in result.output.splitlines() if "vale" in line.lower()] == [
        "vale not on PATH: writing rules not checked"
    ]


@pytest.mark.skipif(VALE_MISSING, reason="vale not on PATH")
def test_vale_findings_are_reported_at_their_level(wiki):
    long_sentence = "This sentence " + "goes on and " * 30 + "ends.\n"
    _note(wiki, "concepts/a.md", "---\ndescription: d\n---\n" + long_sentence)
    # The retired-heading rule guards the issue skeleton. A wiki note may hold the heading.
    _note(wiki, "concepts/b.md", "---\ndescription: d\n---\n## Repo\n\nA short body.\n")
    code, findings = _check()
    assert code == 1
    vale = [f for f in findings if f["check"] == "vale"]
    assert vale and {f["file"] for f in vale} == {"concepts/a.md"}
    assert any(f["severity"] == "error" and "SentenceLength" in f["message"] for f in vale)
    assert all(f["line"] >= 4 for f in vale)


# --- 3. The check for decision records has its own name ---------------------


def test_adr_check_runs_the_decision_record_checks(tmp_path):
    adr = tmp_path / "docs" / "adr"
    adr.mkdir(parents=True)
    (adr / "0014-no-holds.md").write_text("# ADR 0014: a record\n\n## Decision\n\nText.\n")
    result = runner.invoke(app, ["adr", "check", str(tmp_path)])
    assert result.exit_code == 1
    assert "0014-no-holds.md" in result.output
    assert runner.invoke(app, ["adr", "check", str(REPO)]).exit_code == 0


@pytest.mark.parametrize(
    "rel",
    [
        "skills/grilling/ADR-FORMAT.md",
        "docs/how-to/record-a-decision.md",
        ".github/workflows/ci.yml",
        "CONTRIBUTING.md",
    ],
)
def test_docs_and_ci_name_adr_check(rel):
    text = (REPO / rel).read_text()
    assert "lore adr check" in text
    assert "wiki lint" not in text
