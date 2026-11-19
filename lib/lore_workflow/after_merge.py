"""`lore workflow after-merge`: the routine steps after a feature merge.

Pure text edits (board row, ledger line, epic checkbox) plus a thin `GH` seam
for the three GitHub calls; tests replace `GH` so nothing hits the network.
"""

from __future__ import annotations

import re
import subprocess

from lore_workflow.board_parser import BOARD_MARKER, BoardParseError, find_board_table, parse_board
from lore_workflow.build_order_validator import _split_cells
from lore_workflow.ledger import (
    BOARD_LEDGER_HEADING,
    LedgerEntry,
    board_ledger_span,
    format_entry,
    utc_now,
)


def split_ref(ref: str, default_repo: str) -> tuple[str, int]:
    """`(repo, number)` of `owner/repo#n`, `#n` or `n`; a bare number takes *default_repo*."""
    repo, _, num = ref.strip().rpartition("#") if "#" in ref else ("", "", ref.strip())
    if not num.isdigit():
        raise ValueError(f"not an issue reference: {ref!r}")
    return repo or default_repo, int(num)


def mark_merged(board: str, issue: str, sha: str, default_repo: str) -> tuple[str, str, int]:
    """Board with the row of *issue* set to `merged` plus one resume line.

    A board that already holds the resume line for this merge gets no second one.

    Rows match on the full ref. Returns (board, repo, number) of the row.
    Raises ValueError when the board has no row for *issue*.
    """
    want = split_ref(issue, default_repo)
    try:
        parse_board(board)  # rejects a ragged row before any edit
        header, data = find_board_table(board)
    except BoardParseError as exc:
        raise ValueError(str(exc)) from exc
    names = [c.casefold() for c in header]
    state, issue_col = names.index("state"), names.index("issue")
    lines = board.splitlines()
    for lineno, raw in data:
        cells = _split_cells(raw)
        if split_ref(cells[issue_col], default_repo) == want:
            cells[state] = "merged"
            lines[lineno - 1] = "| " + " | ".join(cells) + " |"
            break
    else:
        raise ValueError(f"issue {want[0]}#{want[1]} has no row on the board")
    out = "\n".join(lines) + "\n"
    done = f"#{want[1]} merged at {sha[:7]}"
    if f" {done};" not in out:  # a rerun on a printed board adds no second line
        out = _add_resume(out, done)
    return out, want[0], want[1]


def _add_resume(board: str, state: str) -> str:
    line = format_entry(
        LedgerEntry(
            kind="resume", text=f"{state}; next: dispatch what is ready", timestamp=utc_now()
        )
    )
    lines = board.splitlines()
    span = board_ledger_span(lines)
    if span is None:
        return board.rstrip("\n") + f"\n\n{BOARD_LEDGER_HEADING}\n\n{line}\n"
    start, at = span
    while at > start + 1 and not lines[at - 1].strip():
        at -= 1
    lines.insert(at, line)
    return "\n".join(lines) + "\n"


def tick(body: str, repo: str, issue: int) -> str:
    """Epic body with the open checklist box whose ref is `repo#issue` ticked."""
    return re.sub(
        rf"^(\s*[-*] )\[ \](\s+(?:{re.escape(repo)})?#{issue}\b)",
        r"\1[x]\2",
        body,
        flags=re.MULTILINE,
    )


def _gh(*args: str, stdin: str | None = None) -> str:
    proc = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:2])} failed: {proc.stderr.strip()}")
    return proc.stdout


class _Gh:
    def epic_body(self, repo: str, epic: int) -> str:
        return _gh(
            "issue", "view", str(epic), "-R", repo, "--json", "body", "--jq", ".body"
        ).rstrip("\n")

    def set_epic_body(self, repo: str, epic: int, body: str) -> None:
        _gh("issue", "edit", str(epic), "-R", repo, "--body-file", "-", stdin=body)

    def close(self, repo: str, issue: int, comment: str) -> None:
        _gh("issue", "close", str(issue), "-R", repo, "--comment", comment)


GH = _Gh()

__all__ = ["BOARD_MARKER", "GH", "mark_merged", "tick"]
