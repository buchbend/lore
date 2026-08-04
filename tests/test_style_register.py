"""`lore style show <name>` — whole-file resolution and default-register content.

Resolution is whole-file per wiki: `<wiki>/style/<name>.md` wins, else the
packaged default. Content tests pin the three edits that separate the shipped
register from its draft.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from lore_cli.__main__ import app
from lore_core.style import KNOWN_STYLES, UnknownStyle, resolve_style_path
from typer.testing import CliRunner

runner = CliRunner()

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def lore_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    (tmp_path / "wiki" / "notes").mkdir(parents=True)
    return tmp_path


def _default_text() -> str:
    return resolve_style_path("issue-register").read_text()


# --- resolution ---------------------------------------------------------


def test_known_styles_lists_the_issue_register() -> None:
    assert "issue-register" in KNOWN_STYLES


def test_default_resolves_to_packaged_file(tmp_path: Path) -> None:
    path = resolve_style_path("issue-register", wiki_dir=tmp_path)
    assert path.is_file()
    assert path.read_text().startswith("# Issue Register")


def test_wiki_override_wins(tmp_path: Path) -> None:
    override = tmp_path / "style" / "issue-register.md"
    override.parent.mkdir(parents=True)
    override.write_text("# Our register\n")
    assert resolve_style_path("issue-register", wiki_dir=tmp_path) == override


def test_packaged_default_lives_outside_the_templates_tree() -> None:
    """`lore init` copytree's templates/ into the vault — a copy of the register
    there would look editable while the resolver ignores it."""
    from lore_core.templates import templates_dir

    assert templates_dir() not in resolve_style_path("issue-register").parents


def test_unknown_style_raises_and_names_the_known_ones() -> None:
    with pytest.raises(UnknownStyle) as exc:
        resolve_style_path("prose-register")
    assert "issue-register" in str(exc.value)


# --- CLI ----------------------------------------------------------------


def test_show_prints_the_packaged_default(lore_root: Path) -> None:
    result = runner.invoke(app, ["style", "show", "issue-register"])
    assert result.exit_code == 0, result.output
    assert "# Issue Register" in result.output
    assert "## Batch issues" in result.output


def test_show_prints_the_wiki_override(lore_root: Path) -> None:
    override = lore_root / "wiki" / "notes" / "style" / "issue-register.md"
    override.parent.mkdir(parents=True)
    override.write_text("# Our own register\n")
    result = runner.invoke(app, ["style", "show", "issue-register", "--wiki", "notes"])
    assert result.exit_code == 0, result.output
    assert "Our own register" in result.output
    assert "## Batch issues" not in result.output


def test_show_falls_back_to_default_when_the_wiki_has_no_override(lore_root: Path) -> None:
    result = runner.invoke(app, ["style", "show", "issue-register", "--wiki", "notes"])
    assert result.exit_code == 0, result.output
    assert "# Issue Register" in result.output


def test_show_uses_the_wiki_resolved_from_cwd(lore_root: Path, monkeypatch) -> None:
    """No `--wiki`: the wiki comes from the attached scope of the cwd."""
    from lore_core.types import Scope

    override = lore_root / "wiki" / "notes" / "style" / "issue-register.md"
    override.parent.mkdir(parents=True)
    override.write_text("# Scope-resolved register\n")
    monkeypatch.setattr(
        "lore_cli.style_cmd.resolve_scope",
        lambda cwd: Scope(
            wiki="notes",
            scope="notes:x",
            backend="none",
            claude_md_path=Path("/nowhere/CLAUDE.md"),
        ),
    )
    result = runner.invoke(app, ["style", "show", "issue-register"])
    assert result.exit_code == 0, result.output
    assert "Scope-resolved register" in result.output


def test_show_unknown_style_exits_nonzero_and_names_known_styles(lore_root: Path) -> None:
    result = runner.invoke(app, ["style", "show", "prose-register"])
    assert result.exit_code != 0
    assert "issue-register" in result.output


def test_show_prints_the_file_verbatim(lore_root: Path) -> None:
    """No Rich markup interpretation, no reflow — a linter reads this text."""
    override = lore_root / "wiki" / "notes" / "style" / "issue-register.md"
    override.parent.mkdir(parents=True)
    long_line = "- Banned: [leverage] " + "word " * 40
    override.write_text(long_line + "\n")
    result = runner.invoke(app, ["style", "show", "issue-register", "--wiki", "notes"])
    assert result.exit_code == 0, result.output
    assert long_line in result.output


# --- default register content -------------------------------------------


def test_register_defines_change_and_batch_issue() -> None:
    text = _default_text()
    assert "## Batch issues" in text
    section = text.split("## Batch issues", 1)[1].split("\n## ", 1)[0]
    assert "**Change**" in section
    assert "**Batch issue**" in section
    assert "acceptance criteria" in section
    assert "one PR" in section


def test_rule_14_accepts_code_flavored_provenance() -> None:
    rule = next(line for line in _default_text().splitlines() if line.startswith("14. "))
    for form in ("file path", "command output", "test name"):
        assert form in rule, rule


def test_checkability_claim_is_honest() -> None:
    text = _default_text()
    # The draft's over-claim must be gone.
    assert "Rules 3, 4, 6, 9, 10 and 12 are mechanically checkable" not in text
    claims = text.split("## EARS patterns", 1)[0]
    linted = next(line for line in claims.splitlines() if "lints rules" in line)
    assert re.search(r"rules 3 and 6", linted)
    assert re.search(r"[Rr]ules 9 and 12 .*heuristic", claims)
    assert re.search(r"[Rr]ules 4 and 10 .*review", claims)


def test_register_keeps_the_paste_block_for_consumers_without_lore() -> None:
    text = _default_text()
    assert "## Block for CLAUDE.md and AGENTS.md" in text
    assert "## Issue writing" in text


def test_register_keeps_the_ears_patterns_and_section_skeleton() -> None:
    text = _default_text()
    assert "## EARS patterns for acceptance criteria" in text
    assert "## Required issue structure" in text
    for heading in ("## Context", "## Current behaviour", "## Acceptance criteria"):
        assert heading in text


def test_context_md_defines_the_new_terms() -> None:
    text = (REPO_ROOT / "CONTEXT.md").read_text()
    for term in ("**Writing rules**", "**Change**", "**Batch issue**"):
        assert term in text, term
    assert "**Register**" not in text, "the old term must be gone from the glossary"


# --- the writing rules and the deprecated alias --------------------------


def test_known_styles_lists_the_writing_rules() -> None:
    assert "writing-rules" in KNOWN_STYLES


def test_writing_rules_resolve_to_the_packaged_file(tmp_path: Path) -> None:
    path = resolve_style_path("writing-rules", wiki_dir=tmp_path)
    assert path.name == "writing-rules.md"
    assert path.read_text().startswith("# Writing Rules")


def test_wiki_override_of_the_writing_rules_wins(tmp_path: Path) -> None:
    override = tmp_path / "style" / "writing-rules.md"
    override.parent.mkdir(parents=True)
    override.write_text("# Our own rules\n")
    assert resolve_style_path("writing-rules", wiki_dir=tmp_path) == override


def test_show_prints_the_writing_rules(lore_root: Path) -> None:
    result = runner.invoke(app, ["style", "show", "writing-rules"])
    assert result.exit_code == 0, result.output
    assert "# Writing Rules" in result.stdout


def test_deprecated_alias_prints_the_same_document(lore_root: Path) -> None:
    """Instruction files in other repos still carry the old name."""
    alias = runner.invoke(app, ["style", "show", "issue-register"])
    current = runner.invoke(app, ["style", "show", "writing-rules"])
    assert alias.exit_code == 0, alias.output
    assert alias.stdout == current.stdout


def test_deprecated_alias_writes_one_stderr_line_naming_the_new_name(lore_root: Path) -> None:
    """The document itself goes to stdout, so the notice must not pollute it."""
    result = runner.invoke(app, ["style", "show", "issue-register"])
    lines = result.stderr.strip().splitlines()
    assert len(lines) == 1, result.stderr
    assert "writing-rules" in lines[0]
