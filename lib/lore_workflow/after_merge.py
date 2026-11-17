"""`lore workflow after-merge`: the routine steps after a feature merge.

Pure text edits (board row, ledger line, epic checkbox) plus a thin `GH` seam
for the three GitHub calls; tests replace `GH` so nothing hits the network.
"""

from __future__ import annotations

import re
import subprocess

from lore_workflow.board_parser import BOARD_MARKER, BoardParseError, parse_board
from lore_workflow.build_order_validator import _split_cells
from lore_workflow.ledger import BOARD_LEDGER_HEADING, LedgerEntry, format_entry, utc_now


def split_ref(ref: str, default_repo: str) -> tuple[str, int]:
    """`(repo, number)` of `owner/repo#n`, `#n` or `n`; a bare number takes *default_repo*."""
    repo, _, num = ref.strip().rpartition("#") if "#" in ref else ("", "", ref.strip())
    if not num.isdigit():
        raise ValueError(f"not an issue reference: {ref!r}")
    return repo or default_repo, int(num)


def mark_merged(board: str, issue: str, sha: str, default_repo: str) -> tuple[str, str, int]:
    """Board with the row of *issue* set to `merged` plus one resume line.

    Rows match on the full ref. Returns (board, repo, number) of the row.
    Raises ValueError when the board has no row for *issue*.
    """
    want = split_ref(issue, default_repo)
    try:
        rows = parse_board(board)
    except BoardParseError as exc:
        raise ValueError(str(exc)) from exc
    lines = board.splitlines()
    header = next(i for i, ln in enumerate(lines) if "state" in ln.casefold() and "|" in ln)
    names = [c.casefold() for c in _split_cells(lines[header])]
    state, issue_col = names.index("state"), names.index("issue")
    if not any(split_ref(r.issue, default_repo) == want for r in rows):
        raise ValueError(f"issue {want[0]}#{want[1]} has no row on the board")
    for i in range(header + 2, len(lines)):
        cells = _split_cells(lines[i]) if "|" in lines[i] else []
        if len(cells) > max(state, issue_col) and split_ref(cells[issue_col], default_repo) == want:
            cells[state] = "merged"
            lines[i] = "| " + " | ".join(cells) + " |"
            break
    out = _add_resume("\n".join(lines) + "\n", f"#{want[1]} merged at {sha[:7]}")
    return out, want[0], want[1]


def _add_resume(board: str, state: str) -> str:
    line = format_entry(
        LedgerEntry(
            kind="resume", text=f"{state}; next: dispatch what is ready", timestamp=utc_now()
        )
    )
    lines = board.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.strip() == BOARD_LEDGER_HEADING), None)
    if start is None:
        return board.rstrip("\n") + f"\n\n{BOARD_LEDGER_HEADING}\n\n{line}\n"
    end = next(
        (j for j in range(start + 1, len(lines)) if lines[j].lstrip().startswith("#")), len(lines)
    )
    at = end
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
