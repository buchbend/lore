#!/usr/bin/env python3
"""Parser for the supervision-board comment (peer of ``roadmap_validator.py``).

``/orchestrate-epic`` keeps one durable status comment on the epic issue and
edits it in place across a run — the supervision trail. On resume the run must
read prior per-feature state *structurally*, not by re-reading the Markdown
with the model (scraping is exactly the re-derivation this epic removes).

This module is the machine reader, and — because feature #228 will rewire
orchestrate-epic to EMIT the shape this reads — it is also the contract. The
contract is three things, all explicit here so the emitter can target them:

- **Marker** — the comment is identified by the exact HTML comment
  :data:`BOARD_MARKER`. A comment without it is not a board (a v1 reader must
  refuse a v2 marker rather than silently misread it).
- **Columns** — a GitHub-flavoured Markdown table follows the marker whose
  header carries at least :data:`REQUIRED_COLUMNS` (matched by name,
  case-insensitively; extra columns are tolerated for forward-compat).
- **Rows** — one data row per feature; each row's cell count must match the
  header, so fields never silently misalign.

Any breach raises :class:`BoardParseError` with a human-facing message —
never a partial or misaligned parse. Standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass

from lore_workflow.roadmap_validator import _is_separator, _split_cells

# The comment is identified by this exact marker. Feature #228 emits it
# verbatim; a mismatch (including a future version bump) is "not a board".
BOARD_MARKER = "<!-- lore-orchestrate-epic:status v1 -->"

# Columns a board table must carry. Order here is the canonical emit order;
# parsing maps by name, so an emitter may append columns without breaking us.
REQUIRED_COLUMNS: tuple[str, ...] = ("Feature", "Issue", "Tier", "Batch", "State", "PR")

# PR-cell values that mean "no PR yet" — normalised to "" so a resumed run can
# test truthiness. Mirrors the roadmap validator's "no blocker" placeholders.
_PLACEHOLDER = {"", "-", "–", "—"}


class BoardParseError(ValueError):
    """Raised when a board comment is missing, unmarked, or malformed."""


@dataclass(frozen=True)
class BoardRow:
    """One feature's supervision state, one row of the board table."""

    feature: str
    issue: str
    tier: str
    batch: str
    state: str
    pr: str


def _find_table_after(lines: list[str], start: int) -> tuple[list[str], list[tuple[int, str]]]:
    """Locate the first Markdown table at or after line index *start*.

    A table is a pipe header row immediately followed by a separator row, then
    pipe data rows up to the first blank/non-pipe line. Raises if none found.
    """
    for i in range(start, len(lines) - 1):
        if "|" not in lines[i] or _is_separator(lines[i]):
            continue
        if not _is_separator(lines[i + 1]):
            continue
        header = _split_cells(lines[i])
        data: list[tuple[int, str]] = []
        j = i + 2
        while j < len(lines) and lines[j].strip() and "|" in lines[j]:
            if _is_separator(lines[j]):
                break
            data.append((j + 1, lines[j]))
            j += 1
        return header, data
    raise BoardParseError("no board table found after the status marker")


def _column_index(header: list[str]) -> dict[str, int]:
    """Map each required column to its position in *header* (case-insensitive).

    Raises if any required column is absent, naming the missing ones.
    """
    lower = {cell.casefold(): idx for idx, cell in enumerate(header)}
    missing = [col for col in REQUIRED_COLUMNS if col.casefold() not in lower]
    if missing:
        raise BoardParseError(
            f"board table missing required column(s): {', '.join(missing)}; "
            f"got header {' | '.join(header)}"
        )
    return {col: lower[col.casefold()] for col in REQUIRED_COLUMNS}


def parse_board(text: str) -> list[BoardRow]:
    """Parse a supervision-board comment into structured per-feature rows.

    Raises :class:`BoardParseError` on a missing marker, a missing table,
    missing required columns, or any row whose cell count does not match the
    header — never a silent misread.
    """
    marker_at = text.find(BOARD_MARKER)
    if marker_at < 0:
        raise BoardParseError(
            f"status marker {BOARD_MARKER!r} not found — not a board comment"
        )

    lines = text.splitlines()
    # First line strictly after the marker line.
    marker_line = text.count("\n", 0, marker_at)
    header, data_lines = _find_table_after(lines, marker_line + 1)
    idx = _column_index(header)
    width = len(header)

    rows: list[BoardRow] = []
    for lineno, raw in data_lines:
        cells = _split_cells(raw)
        if len(cells) != width:
            raise BoardParseError(
                f"line {lineno}: board row has {len(cells)} cell(s), "
                f"expected {width} to match the header"
            )
        pr = cells[idx["PR"]].strip()
        rows.append(
            BoardRow(
                feature=cells[idx["Feature"]],
                issue=cells[idx["Issue"]],
                tier=cells[idx["Tier"]],
                batch=cells[idx["Batch"]],
                state=cells[idx["State"]],
                pr="" if pr in _PLACEHOLDER else pr,
            )
        )
    return rows
