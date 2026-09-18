"""`lore migrate flag-blocks` — drop the retired flag blocks from wiki notes.

The blocks were fenced HTML comments an agent appended to a topic note.
The fence is what makes removal exact: every line between the open and
the close goes, every other line stays byte-identical. Dry-run is the
default because the command edits human-readable notes in place.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lore_core.migrate import migrate_strip_flag_blocks
from typer.testing import CliRunner

runner = CliRunner()

BLOCK = (
    "<!-- lore:flag id=ab12cd34ef56 -->\n"
    "**Reported in session: the reaper starves mid-drain.**\n"
    "\n"
    "Two sessions raced the same lock; the loser never retried.\n"
    "\n"
    "_flag · claude · 2026-08-05 · pr 357 (unchecked) · unreviewed_\n"
    "<!-- /lore:flag -->\n"
)

PROSE = "# Reaper\n\nHuman prose above.\n\nHuman prose below.\n"


def _note(wiki: Path, slug: str, body: str) -> Path:
    path = wiki / "concepts" / f"{slug}.md"
    path.write_text(
        "---\nschema_version: 2\ntype: concept\ncreated: 2026-08-01\n"
        "last_reviewed: 2026-08-01\ndescription: x\ntags: []\n---\n" + body,
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    (tmp_path / "wiki" / "private" / "concepts").mkdir(parents=True)
    return tmp_path


def _wiki(vault: Path) -> Path:
    return vault / "wiki" / "private"


def test_dry_run_reports_the_plan_and_writes_nothing(vault: Path) -> None:
    note = _note(_wiki(vault), "reaper", "# Reaper\n\nHuman prose above.\n\n" + BLOCK)
    before = note.read_text(encoding="utf-8")

    result = migrate_strip_flag_blocks(dry_run=True)

    assert result["blocks"] == 1
    assert result["files"] == 1
    assert note.read_text(encoding="utf-8") == before


def test_apply_removes_the_block_and_keeps_every_other_line(vault: Path) -> None:
    note = _note(
        _wiki(vault),
        "reaper",
        "# Reaper\n\nHuman prose above.\n\n" + BLOCK + "\nHuman prose below.\n",
    )

    result = migrate_strip_flag_blocks(dry_run=False)

    after = note.read_text(encoding="utf-8")
    assert result["blocks"] == 1
    assert "<!-- lore:flag" not in after
    assert "<!-- /lore:flag -->" not in after
    assert "the reaper starves mid-drain" not in after
    for line in ("# Reaper", "Human prose above.", "Human prose below.", "tags: []"):
        assert line in after


def test_apply_removes_every_block_in_a_note(vault: Path) -> None:
    second = BLOCK.replace("ab12cd34ef56", "0011223344ff")
    note = _note(_wiki(vault), "reaper", "# Reaper\n\n" + BLOCK + "\nmid\n\n" + second)

    result = migrate_strip_flag_blocks(dry_run=False)

    assert result["blocks"] == 2
    after = note.read_text(encoding="utf-8")
    assert "lore:flag" not in after
    assert "mid" in after


def test_a_note_without_a_block_is_untouched(vault: Path) -> None:
    note = _note(_wiki(vault), "plain", PROSE)
    before = note.read_text(encoding="utf-8")

    result = migrate_strip_flag_blocks(dry_run=False)

    assert result["files"] == 0
    assert note.read_text(encoding="utf-8") == before


def test_an_unterminated_fence_is_left_alone(vault: Path) -> None:
    """Half a block is not a block: dropping to end-of-file would eat prose."""
    body = "# Reaper\n\n<!-- lore:flag id=ab12cd34ef56 -->\n**A lead.**\n\nHuman prose below.\n"
    note = _note(_wiki(vault), "reaper", body)
    before = note.read_text(encoding="utf-8")

    result = migrate_strip_flag_blocks(dry_run=False)

    assert result["blocks"] == 0
    assert note.read_text(encoding="utf-8") == before


def test_rerunning_after_apply_changes_nothing(vault: Path) -> None:
    _note(_wiki(vault), "reaper", "# Reaper\n\n" + BLOCK)

    first = migrate_strip_flag_blocks(dry_run=False)
    second = migrate_strip_flag_blocks(dry_run=False)

    assert first["blocks"] == 1
    assert second["blocks"] == 0


def test_the_wiki_filter_scopes_the_walk(vault: Path) -> None:
    other = vault / "wiki" / "team" / "concepts"
    other.mkdir(parents=True)
    _note(_wiki(vault), "reaper", "# Reaper\n\n" + BLOCK)
    _note(vault / "wiki" / "team", "reaper", "# Reaper\n\n" + BLOCK)

    result = migrate_strip_flag_blocks(wiki_filter="team", dry_run=False)

    assert result["files"] == 1
    assert "lore:flag" in (_wiki(vault) / "concepts" / "reaper.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_is_dry_by_default(vault: Path) -> None:
    from lore_cli.migrate_cmd import app

    note = _note(_wiki(vault), "reaper", "# Reaper\n\n" + BLOCK)
    before = note.read_text(encoding="utf-8")

    result = runner.invoke(app, ["flag-blocks"])

    assert result.exit_code == 0, result.output
    assert note.read_text(encoding="utf-8") == before
    assert "--apply" in result.output


def test_cli_apply_writes(vault: Path) -> None:
    from lore_cli.migrate_cmd import app

    note = _note(_wiki(vault), "reaper", "# Reaper\n\n" + BLOCK)

    result = runner.invoke(app, ["flag-blocks", "--apply"])

    assert result.exit_code == 0, result.output
    assert "lore:flag" not in note.read_text(encoding="utf-8")
