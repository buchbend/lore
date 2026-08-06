"""Supervision-board comment parser (#223).

The board comment is the durable supervision trail `/orchestrate-epic` edits in
place. A resumed run must read prior state structurally, not by model-scraping
the Markdown. This parser is the contract feature #228 will EMIT against: the
marker, the required columns, and this fixture are that contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lore_workflow import board_parser as mod

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "boards"

WELL_FORMED = (FIXTURES / "well-formed.md").read_text(encoding="utf-8")
WITH_NOTES_SECTION = (FIXTURES / "with-notes-section.md").read_text(encoding="utf-8")


def test_marker_and_columns_are_exported_contract() -> None:
    # #228 emits against these constants; keep them importable and stable.
    assert mod.BOARD_MARKER == "<!-- lore-orchestrate-epic:status v1 -->"
    assert mod.REQUIRED_COLUMNS == ("Feature", "Issue", "Tier", "Batch", "State", "PR")


def test_parses_all_rows() -> None:
    rows = mod.parse_board(WELL_FORMED)
    assert len(rows) == 3


def test_row_fields_mapped_by_column() -> None:
    rows = mod.parse_board(WELL_FORMED)
    first = rows[0]
    assert first.feature == "Load the config"
    assert first.issue == "ccatobs/widget#12"
    assert first.tier == "AFK"
    assert first.batch == "1"
    assert first.state == "merged"
    assert first.pr == "ccatobs/widget#40"


def test_placeholder_pr_normalized_to_empty() -> None:
    rows = mod.parse_board(WELL_FORMED)
    # em-dash placeholder in the PR column means "no PR yet"
    assert rows[1].pr == ""
    assert rows[2].pr == ""


def test_resumed_run_can_select_by_state() -> None:
    rows = mod.parse_board(WELL_FORMED)
    merged = [r.issue for r in rows if r.state == "merged"]
    assert merged == ["ccatobs/widget#12"]


def test_notes_section_does_not_change_parsed_rows() -> None:
    # #379 moves the orchestrator's supervision narrative onto the board: a
    # "## Notes" section now follows the table in the same comment. The
    # parser must keep reading the table structurally and ignore the notes
    # section, so a board with notes yields the same rows as one without.
    assert mod.parse_board(WITH_NOTES_SECTION) == mod.parse_board(WELL_FORMED)


# --- malformed inputs raise, never silently misread ------------------------


def test_missing_marker_raises() -> None:
    text = WELL_FORMED.replace(mod.BOARD_MARKER, "")
    with pytest.raises(mod.BoardParseError, match="marker"):
        mod.parse_board(text)


def test_missing_required_column_raises() -> None:
    text = (
        f"{mod.BOARD_MARKER}\n\n"
        "| Feature | Issue | Tier | Batch | PR |\n"
        "|---|---|---|---|---|\n"
        "| a | o/r#1 | AFK | 1 | — |\n"
    )
    with pytest.raises(mod.BoardParseError, match="State"):
        mod.parse_board(text)


def test_no_table_after_marker_raises() -> None:
    with pytest.raises(mod.BoardParseError, match="table"):
        mod.parse_board(f"{mod.BOARD_MARKER}\n\njust prose, no table here\n")


def test_malformed_row_raises() -> None:
    # A data row with too few cells must error, not drop or misalign fields.
    text = (
        f"{mod.BOARD_MARKER}\n\n"
        "| Feature | Issue | Tier | Batch | State | PR |\n"
        "|---|---|---|---|---|---|\n"
        "| a | o/r#1 | AFK | 1 | merged |\n"  # missing PR cell
    )
    with pytest.raises(mod.BoardParseError, match="cell"):
        mod.parse_board(text)


# --- CLI -------------------------------------------------------------------


def test_parse_board_cmd_emits_json(capsys) -> None:
    from lore_cli import workflow_cmd

    rc = workflow_cmd.main(["parse-board", str(FIXTURES / "well-formed.md")])
    assert rc == 0
    import json

    payload = json.loads(capsys.readouterr().out)
    assert [r["issue"] for r in payload["rows"]] == [
        "ccatobs/widget#12",
        "ccatobs/widget#13",
        "ccatobs/widget#14",
    ]


def test_parse_board_cmd_errors_on_malformed(capsys) -> None:
    from lore_cli import workflow_cmd

    rc = workflow_cmd.main(["parse-board", str(FIXTURES / "missing-marker.md")])
    assert rc == 1
    err = capsys.readouterr().err
    assert "marker" in err


def test_parse_board_cmd_tolerates_notes_section(capsys) -> None:
    from lore_cli import workflow_cmd

    rc = workflow_cmd.main(["parse-board", str(FIXTURES / "with-notes-section.md")])
    assert rc == 0
    import json

    payload = json.loads(capsys.readouterr().out)
    assert [r["issue"] for r in payload["rows"]] == [
        "ccatobs/widget#12",
        "ccatobs/widget#13",
        "ccatobs/widget#14",
    ]
