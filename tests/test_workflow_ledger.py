"""Tests for the build-run ledger (PRD 0015 § Ledger and breakpoints)."""

from __future__ import annotations

import pytest
from lore_workflow.ledger import LedgerEntry, format_entry, parse_ledger

LEDGER = """\
# Lore ledger

- [adr] open — Store tokens per skill phase
- [term] approved — breakpoint
- [left] filed buchbend/lore#123 — Retry flaky capture test
- [left] dropped — Rename the board marker
- [resume] 2026-09-29T10:00:00Z — round 2 merged; next: wire the hook
"""


def test_parse_ledger_reads_all_four_kinds() -> None:
    entries = parse_ledger(LEDGER)
    assert [(e.kind, e.outcome, e.text) for e in entries] == [
        ("adr", "open", "Store tokens per skill phase"),
        ("term", "approved", "breakpoint"),
        ("left", "filed buchbend/lore#123", "Retry flaky capture test"),
        ("left", "dropped", "Rename the board marker"),
        ("resume", None, "round 2 merged; next: wire the hook"),
    ]
    assert entries[-1].timestamp == "2026-09-29T10:00:00Z"


def test_format_round_trips_every_line() -> None:
    entries = parse_ledger(LEDGER)
    lines = [format_entry(e) for e in entries]
    assert lines == [ln for ln in LEDGER.splitlines() if ln.startswith("- [")]
    assert parse_ledger("\n".join(lines)) == entries


def test_format_flattens_newlines_in_text() -> None:
    line = format_entry(LedgerEntry(kind="adr", outcome="open", text="two\nlines"))
    assert line == "- [adr] open — two lines"


def test_malformed_entry_line_fails_closed() -> None:
    from lore_workflow.ledger import LedgerParseError

    with pytest.raises(LedgerParseError, match="line 2"):
        parse_ledger("# Ledger\n- [adr] maybe — undecided\n")


def test_append_entry_creates_file_with_heading(tmp_path) -> None:
    from lore_workflow.ledger import append_entry

    path = tmp_path / "lore-ledger.md"
    append_entry(path, LedgerEntry(kind="adr", outcome="open", text="first"))
    append_entry(path, LedgerEntry(kind="left", outcome="open", text="second"))
    text = path.read_text()
    assert text.startswith("# Lore ledger\n")
    assert [e.text for e in parse_ledger(text)] == ["first", "second"]


def test_set_outcome_rewrites_one_line_by_index_or_match(tmp_path) -> None:
    from lore_workflow.ledger import set_outcome

    path = tmp_path / "lore-ledger.md"
    path.write_text(LEDGER)
    set_outcome(path, "1", "approved")
    set_outcome(path, "rename the BOARD", "filed buchbend/lore#9")
    entries = parse_ledger(path.read_text())
    assert entries[0].outcome == "approved"
    assert entries[3].outcome == "filed buchbend/lore#9"
    assert path.read_text().startswith("# Lore ledger\n\n- [adr] approved")


def test_set_outcome_refuses_ambiguous_match(tmp_path) -> None:
    from lore_workflow.ledger import set_outcome

    path = tmp_path / "lore-ledger.md"
    path.write_text(LEDGER)
    with pytest.raises(ValueError, match="ledger lines match"):
        set_outcome(path, "e", "dropped")
    with pytest.raises(ValueError, match="resume"):
        set_outcome(path, "5", "dropped")


def test_find_git_dir_in_plain_repo_and_linked_worktree(tmp_path) -> None:
    from lore_workflow.ledger import default_ledger_path, find_git_dir

    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "src" / "pkg").mkdir(parents=True)
    assert find_git_dir(repo / "src" / "pkg") == repo / ".git"

    wt_gitdir = repo / ".git" / "worktrees" / "wt"
    wt_gitdir.mkdir(parents=True)
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {wt_gitdir}\n")
    assert find_git_dir(wt) == wt_gitdir
    assert default_ledger_path(wt) == wt_gitdir / "lore-ledger.md"

    assert find_git_dir(tmp_path) is None


BOARD = """\
<!-- lore-orchestrate-epic:status v1 -->
## Supervision board

| Feature | Issue | Tier | Batch | State | PR |
|---|---|---|---|---|---|
| Ledger | buchbend/lore#445 | mid | 1 | merged | #450 |

## Ledger

- [adr] approved — Ledger lives in the git dir
- [term] open — risk level

## Notes

- [x] not a ledger line in notes; prose is ignored
"""


def test_parse_board_ledger_reads_only_the_ledger_section() -> None:
    from lore_workflow.ledger import parse_board_ledger

    entries = parse_board_ledger(BOARD)
    assert [(e.kind, e.outcome) for e in entries] == [("adr", "approved"), ("term", "open")]
    assert entries[1].line == 11


def test_parse_board_ledger_without_section_is_empty() -> None:
    from lore_workflow.ledger import parse_board_ledger

    assert parse_board_ledger(BOARD.split("## Ledger")[0]) == []
