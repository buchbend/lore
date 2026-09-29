"""CLI tests for `lore workflow ledger-add|ledger-set|ledger-check` and the
ledger key of `parse-board` (PRD 0015 § Ledger and breakpoints)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from lore_cli import workflow_cmd

BOARD = """\
<!-- lore-orchestrate-epic:status v1 -->

| Feature | Issue | Tier | Batch | State | PR |
|---|---|---|---|---|---|
| Ledger | buchbend/lore#445 | AFK | 1 | merged | buchbend/lore#450 |

## Ledger

- [adr] approved — Ledger lives in the git dir
- [term] open — risk level
- [resume] 2026-09-29T10:00:00Z — feature 1 merged; next: feature 2

## Notes

- Blocker: none.
"""


def _stdin(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(text))


def test_ledger_add_then_check_names_the_open_line(tmp_path: Path, capsys) -> None:
    ledger = tmp_path / "lore-ledger.md"
    assert (
        workflow_cmd.main(
            [
                "ledger-add",
                "--kind",
                "adr",
                "--text",
                "Keep risk deterministic",
                "--path",
                str(ledger),
            ]
        )
        == 0
    )
    assert (
        workflow_cmd.main(
            [
                "ledger-add",
                "--kind",
                "term",
                "--text",
                "breakpoint",
                "--outcome",
                "approved",
                "--path",
                str(ledger),
            ]
        )
        == 0
    )
    capsys.readouterr()

    rc = workflow_cmd.main(["ledger-check", str(ledger)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "Keep risk deterministic" in out
    assert "breakpoint" not in out


def test_ledger_check_passes_when_every_line_has_an_outcome(tmp_path: Path, capsys) -> None:
    ledger = tmp_path / "lore-ledger.md"
    ledger.write_text(
        "- [adr] approved — a\n- [left] filed buchbend/lore#1 — b\n- [term] dropped — c\n"
        "- [resume] 2026-09-29T10:00:00Z — done; next: ship\n"
    )
    assert workflow_cmd.main(["ledger-check", str(ledger)]) == 0


def test_ledger_check_missing_ledger_passes_with_note(tmp_path: Path, capsys) -> None:
    rc = workflow_cmd.main(["ledger-check", str(tmp_path / "absent.md")])
    assert rc == 0
    assert "no ledger" in capsys.readouterr().out


def test_ledger_check_reads_board_ledger_from_stdin(monkeypatch, capsys) -> None:
    _stdin(monkeypatch, BOARD)
    rc = workflow_cmd.main(["ledger-check", "-"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "risk level" in out
    assert "Ledger lives in the git dir" not in out


def test_ledger_check_fails_on_malformed_line(tmp_path: Path, capsys) -> None:
    ledger = tmp_path / "lore-ledger.md"
    ledger.write_text("- [adr] later — undecided\n")
    rc = workflow_cmd.main(["ledger-check", str(ledger)])
    assert rc == 1
    assert "invalid outcome" in capsys.readouterr().err


def test_ledger_check_defaults_to_the_git_dir_ledger(tmp_path, monkeypatch, capsys) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "lore-ledger.md").write_text("- [left] open — flaky test\n")
    monkeypatch.chdir(tmp_path)
    rc = workflow_cmd.main(["ledger-check"])
    assert rc == 1
    assert "flaky test" in capsys.readouterr().out


def test_ledger_add_resume_line_carries_timestamp_and_next(tmp_path: Path) -> None:
    ledger = tmp_path / "lore-ledger.md"
    rc = workflow_cmd.main(
        [
            "ledger-add",
            "--kind",
            "resume",
            "--text",
            "round 2 merged",
            "--next",
            "wire the hook",
            "--path",
            str(ledger),
        ]
    )
    assert rc == 0
    line = ledger.read_text().splitlines()[-1]
    assert line.startswith("- [resume] 20")
    assert line.endswith(" — round 2 merged; next: wire the hook")


def test_ledger_add_rejects_bad_outcome(tmp_path: Path, capsys) -> None:
    rc = workflow_cmd.main(
        [
            "ledger-add",
            "--kind",
            "adr",
            "--text",
            "x",
            "--outcome",
            "maybe",
            "--path",
            str(tmp_path / "l.md"),
        ]
    )
    assert rc == 1
    assert "invalid outcome" in capsys.readouterr().err
    assert not (tmp_path / "l.md").exists()


def test_ledger_set_resolves_the_open_line(tmp_path: Path, capsys) -> None:
    ledger = tmp_path / "lore-ledger.md"
    ledger.write_text("- [adr] open — Keep risk deterministic\n")
    rc = workflow_cmd.main(
        ["ledger-set", "risk", "--outcome", "filed buchbend/lore#500", "--path", str(ledger)]
    )
    assert rc == 0
    assert workflow_cmd.main(["ledger-check", str(ledger)]) == 0


def test_parse_board_emits_ledger_lines(monkeypatch, capsys) -> None:
    _stdin(monkeypatch, BOARD)
    rc = workflow_cmd.main(["parse-board", "-"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert [r["issue"] for r in payload["rows"]] == ["buchbend/lore#445"]
    assert payload["ledger"] == [
        {
            "kind": "adr",
            "outcome": "approved",
            "timestamp": None,
            "text": "Ledger lives in the git dir",
        },
        {"kind": "term", "outcome": "open", "timestamp": None, "text": "risk level"},
        {
            "kind": "resume",
            "outcome": None,
            "timestamp": "2026-09-29T10:00:00Z",
            "text": "feature 1 merged; next: feature 2",
        },
    ]


def test_ledger_add_to_stdout_for_a_board_ledger(capsys) -> None:
    rc = workflow_cmd.main(["ledger-add", "--kind", "left", "--text", "docs pass", "--path", "-"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "- [left] open — docs pass"


def test_ledger_archive_renames_a_finished_ledger(tmp_path, monkeypatch, capsys) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "lore-ledger.md").write_text("- [adr] approved — a\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("lore_workflow.ledger.utc_date", lambda: "2026-09-29")
    assert workflow_cmd.main(["ledger-archive"]) == 0
    assert not (git_dir / "lore-ledger.md").exists()
    archived = git_dir / "lore-ledger.2026-09-29.done.md"
    assert archived.read_text() == "- [adr] approved — a\n"
    assert str(archived) in capsys.readouterr().out


def test_ledger_archive_keeps_an_earlier_archive_of_the_same_day(tmp_path, monkeypatch) -> None:
    ledger = tmp_path / "lore-ledger.md"
    ledger.write_text("- [term] dropped — b\n")
    (tmp_path / "lore-ledger.2026-09-29.done.md").write_text("earlier\n")
    monkeypatch.setattr("lore_workflow.ledger.utc_date", lambda: "2026-09-29")
    assert workflow_cmd.main(["ledger-archive", "--path", str(ledger)]) == 0
    assert (tmp_path / "lore-ledger.2026-09-29.done.md").read_text() == "earlier\n"
    assert (tmp_path / "lore-ledger.2026-09-29-2.done.md").read_text() == "- [term] dropped — b\n"


def test_ledger_archive_refuses_a_ledger_with_open_lines(tmp_path, capsys) -> None:
    ledger = tmp_path / "lore-ledger.md"
    ledger.write_text("- [left] open — flaky test\n")
    assert workflow_cmd.main(["ledger-archive", "--path", str(ledger)]) == 1
    assert ledger.exists()
    assert "open" in capsys.readouterr().err


def test_ledger_archive_without_ledger_passes_with_note(tmp_path, capsys) -> None:
    assert workflow_cmd.main(["ledger-archive", "--path", str(tmp_path / "lore-ledger.md")]) == 0
    assert "no ledger" in capsys.readouterr().out


# --- ledger-set on a board comment (epic mode) ------------------------------


def test_ledger_set_board_rewrites_the_line_and_prints_the_whole_body(monkeypatch, capsys) -> None:
    _stdin(monkeypatch, BOARD)
    rc = workflow_cmd.main(["ledger-set", "2", "--outcome", "approved", "--board", "-"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out == BOARD.replace("- [term] open — risk level", "- [term] approved — risk level")

    _stdin(monkeypatch, out)
    assert workflow_cmd.main(["ledger-check", "-"]) == 0


def test_ledger_set_board_selects_by_text(monkeypatch, capsys) -> None:
    _stdin(monkeypatch, BOARD)
    rc = workflow_cmd.main(
        ["ledger-set", "risk", "--outcome", "filed buchbend/lore#500", "--board", "-"]
    )
    assert rc == 0
    assert "- [term] filed buchbend/lore#500 — risk level" in capsys.readouterr().out


def test_ledger_set_board_leaves_a_same_text_line_outside_the_ledger(monkeypatch, capsys) -> None:
    board = BOARD.replace("- Blocker: none.", "- [term] open — risk level")
    _stdin(monkeypatch, board)
    assert workflow_cmd.main(["ledger-set", "2", "--outcome", "dropped", "--board", "-"]) == 0
    out = capsys.readouterr().out
    assert out.count("- [term] open — risk level") == 1
    assert out.index("- [term] dropped — risk level") < out.index("## Notes")


def test_ledger_set_board_refuses_a_resume_line(monkeypatch, capsys) -> None:
    _stdin(monkeypatch, BOARD)
    assert workflow_cmd.main(["ledger-set", "3", "--outcome", "approved", "--board", "-"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "resume" in captured.err


def test_ledger_set_board_takes_only_stdin(capsys) -> None:
    rc = workflow_cmd.main(["ledger-set", "1", "--outcome", "approved", "--board", "board.md"])
    assert rc == 1
    assert "--board -" in capsys.readouterr().err


def test_ledger_check_fails_on_empty_stdin(monkeypatch, capsys) -> None:
    """A failed `gh api … --jq .body` pipes nothing; the epic gate must not pass."""
    _stdin(monkeypatch, "  \n")
    rc = workflow_cmd.main(["ledger-check", "-"])
    assert rc == 1
    assert "no input on stdin" in capsys.readouterr().err
