#!/usr/bin/env python3
"""Roadmap-DAG validator for the epic workflow (peer of ``prd_docs.py``).

The ``/to-epic`` skill emits, and ``/orchestrate-epic`` consumes, a Markdown
*roadmap table* in the epic issue body: the canonical dependency DAG, one row
per feature/sub-issue. That table drives autonomous dispatch, so a malformed
table is not a cosmetic problem — it derails the orchestrator. This module is
the deterministic gate both skills run: ``/to-epic`` before publishing the epic,
``/orchestrate-epic`` before dispatching any teammate.

Like the ``prd_docs`` peer it is self-contained — standard library only, so it
runs in CI with no install step — and does no GitHub I/O: the caller supplies the
epic body text it already has (``gh issue view <epic> --json body``).

The roadmap table's columns are exactly, in order::

    | # | Feature | Issue | Repo | Type | Blocked by |

and the validator checks the four dimensions that make the table a usable DAG:

  1. **columns** — the header is exactly the required columns, in order;
  2. **fully-qualified Issue refs** — every ``Issue`` cell is ``owner/repo#n``
     (a bare ``#n`` or ``repo#n`` is rejected), so cross-repo refs are
     unambiguous;
  3. **resolvable edges** — every ``Blocked by`` reference resolves to a row in
     the table (a fully-qualified ref matches that row's Issue; a bare ``#n``
     matches by number). An em-dash, a hyphen, or an empty cell means "no
     blocker";
  4. **acyclic** — the blocked-by graph has no cycle.

``validate_roadmap`` returns a structured :class:`ValidationResult` (never
raises on invalid input); ``validate_roadmap_or_raise`` raises
:class:`RoadmapError` carrying the same problems, for callers that prefer
exceptions. ``main`` is a thin CLI so a skill can run the file as a script and
gate on its exit code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# The roadmap table's required columns, in canonical order. This is the single
# source of truth for both the header check and the column->cell mapping the
# deeper checks rely on.
REQUIRED_COLUMNS: tuple[str, ...] = (
    "#",
    "Feature",
    "Issue",
    "Repo",
    "Type",
    "Blocked by",
)

# A fully-qualified issue ref: owner/repo#number. owner and repo use the
# GitHub-name character set (letters, digits, dot, underscore, hyphen). A ref
# without the owner/repo prefix (bare "#12" or "repo#12") is deliberately not
# matched — cross-repo edges must be unambiguous.
_FQ_REF = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+#\d+$")

# A short blocked-by token referencing a row by number: "#12" or "12".
_SHORT_REF = re.compile(r"^#?(\d+)$")

# The trailing "#number" of an issue ref, used to index rows by number so a
# short blocked-by token can resolve to a row.
_TRAILING_NUM = re.compile(r"#(\d+)\s*$")

# A Markdown table separator cell: dashes with optional alignment colons.
_SEP_CELL = re.compile(r"^:?-+:?$")

# Cell values that mean "no blocker": empty, hyphen, en-dash, em-dash.
_NO_BLOCKER = {"", "-", "–", "—"}


@dataclass(frozen=True)
class RoadmapRow:
    """One parsed feature row of the roadmap table."""

    number: str
    feature: str
    issue: str
    repo: str
    type: str
    blocked_by: tuple[str, ...]
    lineno: int


@dataclass(frozen=True)
class Problem:
    """A single validation failure.

    ``kind`` is a stable machine token (``missing_table``, ``columns``,
    ``non_fq_ref``, ``dangling_edge``, ``cycle``); ``message`` is human-facing.
    """

    kind: str
    message: str


@dataclass
class ValidationResult:
    """The outcome of validating a roadmap table.

    ``ok`` is true only when ``problems`` is empty. ``rows`` holds the parsed
    feature rows (empty when no table was found or the columns were wrong).
    """

    ok: bool
    problems: list[Problem] = field(default_factory=list)
    rows: list[RoadmapRow] = field(default_factory=list)


class RoadmapError(ValueError):
    """Raised by :func:`validate_roadmap_or_raise` for an invalid roadmap.

    Carries the full :class:`ValidationResult` (``.result``) and its
    ``.problems`` so a caller catching the exception still gets structured
    detail.
    """

    def __init__(self, result: ValidationResult) -> None:
        self.result = result
        self.problems = result.problems
        summary = "; ".join(f"{p.kind}: {p.message}" for p in result.problems)
        super().__init__(summary or "roadmap is invalid")


def _split_cells(line: str) -> list[str]:
    """Split a Markdown table row into stripped cell values.

    Leading/trailing pipes are optional (GitHub-flavoured Markdown). Escaped
    pipes are not handled — roadmap cells never contain them.
    """
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [cell.strip() for cell in s.split("|")]


def _is_separator(line: str) -> bool:
    """True if *line* is a Markdown table separator (``|---|---|``)."""
    if "|" not in line:
        return False
    cells = _split_cells(line)
    return bool(cells) and all(_SEP_CELL.match(cell) for cell in cells)


def _parse_blockers(cell: str) -> tuple[str, ...]:
    """Parse a ``Blocked by`` cell into blocker tokens.

    Splits on commas; an em-dash, hyphen, en-dash, or empty part means "no
    blocker" and contributes nothing.
    """
    tokens: list[str] = []
    for part in cell.split(","):
        token = part.strip()
        if token in _NO_BLOCKER:
            continue
        tokens.append(token)
    return tuple(tokens)


def _find_table(markdown: str) -> tuple[list[str], list[tuple[int, str]]] | None:
    """Locate the first Markdown table and return (header cells, data rows).

    A table is a pipe row immediately followed by a separator row; data rows are
    the pipe rows that follow, up to the first blank or non-pipe line. Returns
    ``None`` when no table is present. Data rows are ``(1-based lineno, raw)``.
    """
    lines = markdown.splitlines()
    for i in range(len(lines) - 1):
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
    return None


def _check_columns(header: list[str]) -> Problem | None:
    """Return a columns Problem if *header* is not the required column set."""
    got = [cell.lower() for cell in header]
    want = [cell.lower() for cell in REQUIRED_COLUMNS]
    if got != want:
        return Problem(
            "columns",
            "roadmap columns must be exactly "
            f"'{' | '.join(REQUIRED_COLUMNS)}'; got '{' | '.join(header)}'",
        )
    return None


def _build_indexes(
    rows: list[RoadmapRow],
) -> tuple[dict[str, RoadmapRow], dict[int, list[RoadmapRow]]]:
    """Index rows by full Issue ref and by trailing issue number.

    ``by_full`` maps ``owner/repo#n`` to its row (first occurrence wins).
    ``by_number`` maps the bare number to every row carrying it — a list,
    because cross-repo roadmaps can reuse a number across repos.
    """
    by_full: dict[str, RoadmapRow] = {}
    by_number: dict[int, list[RoadmapRow]] = {}
    for row in rows:
        by_full.setdefault(row.issue, row)
        match = _TRAILING_NUM.search(row.issue)
        if match:
            by_number.setdefault(int(match.group(1)), []).append(row)
    return by_full, by_number


def _resolve(
    token: str,
    by_full: dict[str, RoadmapRow],
    by_number: dict[int, list[RoadmapRow]],
) -> list[RoadmapRow]:
    """Return the rows a single blocked-by *token* resolves to (empty if none).

    A fully-qualified token matches one row by exact Issue ref; a short ``#n``
    token matches by number (possibly several rows across repos).
    """
    token = token.strip()
    if _FQ_REF.match(token):
        row = by_full.get(token)
        return [row] if row is not None else []
    match = _SHORT_REF.match(token)
    if match:
        return list(by_number.get(int(match.group(1)), ()))
    return []


def _find_cycle(
    rows: list[RoadmapRow],
    by_full: dict[str, RoadmapRow],
    by_number: dict[int, list[RoadmapRow]],
) -> list[str] | None:
    """Return a blocked-by cycle as a list of Issue refs, or ``None`` if acyclic.

    Builds the directed graph (row -> each row it is blocked by) over resolvable
    edges only, then does a depth-first search marking nodes on the active path;
    a back edge to a node still on the path closes a cycle.
    """
    position = {id(row): idx for idx, row in enumerate(rows)}
    adjacency: dict[int, list[int]] = {idx: [] for idx in range(len(rows))}
    for idx, row in enumerate(rows):
        seen: set[int] = set()
        for token in row.blocked_by:
            for blocker in _resolve(token, by_full, by_number):
                target = position[id(blocker)]
                if target not in seen:
                    seen.add(target)
                    adjacency[idx].append(target)

    white, grey, black = 0, 1, 2
    color = [white] * len(rows)
    path: list[int] = []

    def visit(node: int) -> list[int] | None:
        color[node] = grey
        path.append(node)
        for nxt in adjacency[node]:
            if color[nxt] == grey:
                start = path.index(nxt)
                return path[start:] + [nxt]
            if color[nxt] == white:
                found = visit(nxt)
                if found is not None:
                    return found
        path.pop()
        color[node] = black
        return None

    for start_node in range(len(rows)):
        if color[start_node] == white:
            cycle = visit(start_node)
            if cycle is not None:
                return [rows[idx].issue or rows[idx].number for idx in cycle]
    return None


def validate_roadmap(markdown: str) -> ValidationResult:
    """Validate the roadmap table in *markdown* and return a structured result.

    Never raises on invalid input — every failure is a :class:`Problem` on the
    returned :class:`ValidationResult`. When the header columns are wrong the
    column->cell mapping is untrustworthy, so only the column problem is
    reported (the deeper checks are skipped).
    """
    parsed = _find_table(markdown)
    if parsed is None:
        return ValidationResult(
            ok=False,
            problems=[Problem("missing_table", "no roadmap table found in the text")],
        )

    header, data_lines = parsed
    column_problem = _check_columns(header)
    if column_problem is not None:
        return ValidationResult(ok=False, problems=[column_problem])

    problems: list[Problem] = []
    rows: list[RoadmapRow] = []
    width = len(REQUIRED_COLUMNS)
    for lineno, raw in data_lines:
        cells = _split_cells(raw)
        if len(cells) != width:
            problems.append(
                Problem(
                    "columns",
                    f"roadmap row at line {lineno} has {len(cells)} cells, "
                    f"expected {width}",
                )
            )
            continue
        rows.append(
            RoadmapRow(
                number=cells[0],
                feature=cells[1],
                issue=cells[2],
                repo=cells[3],
                type=cells[4],
                blocked_by=_parse_blockers(cells[5]),
                lineno=lineno,
            )
        )

    for row in rows:
        if not _FQ_REF.match(row.issue):
            problems.append(
                Problem(
                    "non_fq_ref",
                    f"row {row.number}: Issue ref '{row.issue}' is not "
                    "fully-qualified (expected owner/repo#n)",
                )
            )

    by_full, by_number = _build_indexes(rows)
    for row in rows:
        for token in row.blocked_by:
            if not _resolve(token, by_full, by_number):
                problems.append(
                    Problem(
                        "dangling_edge",
                        f"row {row.number}: blocked-by '{token}' does not "
                        "resolve to any roadmap row",
                    )
                )

    cycle = _find_cycle(rows, by_full, by_number)
    if cycle is not None:
        problems.append(
            Problem("cycle", "cyclic blocked-by chain: " + " -> ".join(cycle))
        )

    return ValidationResult(ok=not problems, problems=problems, rows=rows)


def validate_roadmap_or_raise(markdown: str) -> ValidationResult:
    """Validate *markdown*, raising :class:`RoadmapError` if it is invalid.

    Returns the passing :class:`ValidationResult` on success.
    """
    result = validate_roadmap(markdown)
    if not result.ok:
        raise RoadmapError(result)
    return result


def main(argv: list[str] | None = None) -> int:
    """CLI: validate a roadmap table, printing problems and returning an exit code.

    Reads the epic body from a file path argument, or from stdin when the path
    is omitted or ``-``. Returns 0 when the roadmap is valid, 1 otherwise, so a
    skill can gate on the exit code.
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description=(
            "Validate an epic roadmap table: required columns, fully-qualified "
            "owner/repo#n refs, resolvable blocked-by edges, acyclic DAG."
        )
    )
    parser.add_argument(
        "path",
        nargs="?",
        help="Markdown file to read (the epic body); omit or '-' to read stdin.",
    )
    args = parser.parse_args(argv)

    if args.path and args.path != "-":
        text = Path(args.path).read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()

    result = validate_roadmap(text)
    if result.ok:
        print(f"roadmap OK: {len(result.rows)} feature(s), dependency DAG is acyclic")
        return 0
    print("roadmap INVALID:", file=sys.stderr)
    for problem in result.problems:
        print(f"  - {problem.kind}: {problem.message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
