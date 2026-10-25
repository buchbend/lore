"""The build-run ledger: ADR candidates, term candidates, left items, resume lines.

PRD 0015 § Ledger and breakpoints, ADR 0014. A build run keeps one ledger.
The ledger check blocks the finish point while any non-resume line is still
``open``. Resume lines let a run continue after ``/clear`` or compaction.

Line format
===========

One Markdown list item per line. The parser ignores every other line, so a
ledger file may carry a heading or prose::

    - [adr] open — Store tokens per skill phase
    - [term] approved — breakpoint
    - [left] filed buchbend/lore#123 — Retry the flaky capture test
    - [left] dropped — Rename the board marker
    - [resume] 2026-09-29T10:00:00Z — round 2 merged; next: wire the hook
    - [logbook] file=delivered pm=undelivered — {"key":"lore:acme/widget:#7",...}
    - [board] https://github.com/acme/widget/issues/7#issuecomment-42 — acme/widget#7

- ``kind`` sits in brackets: ``adr``, ``term``, ``left``, ``resume``, ``logbook``
  or ``board``.
- An ``adr``, ``term`` or ``left`` line carries its outcome next: ``open``,
  ``approved``, ``dropped`` or ``filed <owner/repo#n>``.
- A resume line carries an ISO 8601 UTC timestamp instead of an outcome.
  Its text is the state, then ``; next:`` and the next ask.
- A logbook line (PRD 0017) carries ``<sink>=delivered|undelivered`` per sink
  and the entry as JSON (:mod:`lore_workflow.logbook`). It is never open, so
  it never blocks the check. :func:`archive_ledger` carries an undelivered
  one into the new ledger.
- A board line carries the URL of an epic's board comment, then the epic.
  It is never open. PreCompact and SessionStart name the board from it, so an
  epic run finds its ledger again after compaction.
- An em dash with a space on each side (`` — ``) separates the text.
  The text runs to the end of the line.

A line that starts with ``- [`` but breaks this format raises
:class:`LedgerParseError`. The check fails closed and never skips a line.

Homes
=====

- ``loop`` and ``issue`` mode: ``$(git rev-parse --git-dir)/lore-ledger.md``.
  In a linked worktree that is the per-worktree git dir, so each worktree has
  its own ledger. :func:`find_git_dir` resolves it without a subprocess.
- ``epic`` mode: a ``## Ledger`` section in the board comment, after the
  table and before ``## Notes``. :func:`parse_board_ledger` reads it.

At a loop or issue finish point, :func:`archive_ledger` renames the file to
``lore-ledger.<UTC-date>.done.md`` so the SessionStart resume offer stops.

Standard library only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

KINDS: tuple[str, ...] = ("adr", "term", "left", "resume", "logbook", "board")

#: File name of the loop/issue ledger inside the git dir.
LEDGER_FILENAME = "lore-ledger.md"

#: Heading of the ledger section in an epic board comment.
BOARD_LEDGER_HEADING = "## Ledger"

SEP = " — "

_OUTCOME_RE = re.compile(r"^(open|approved|dropped|filed [\w.-]+/[\w.-]+#\d+)$")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:\d{2})?$")
_URL_RE = re.compile(r"^https?://\S+$")
_DELIVERY_RE = re.compile(r"^[\w.-]+=(un)?delivered( [\w.-]+=(un)?delivered)*$")
_LINE_RE = re.compile(r"^- \[(?P<kind>[a-z]+)\] (?P<status>.+?) — (?P<text>.*)$")


class LedgerParseError(ValueError):
    """Raised when a ledger line starts like an entry but breaks the format."""


@dataclass(frozen=True)
class LedgerEntry:
    """One ledger line. ``outcome`` is None on a resume line, ``timestamp``
    is None on every other kind. ``line`` is the 1-based source line, or 0
    for an entry built in code; it takes no part in equality."""

    kind: str
    text: str
    outcome: str | None = None
    timestamp: str | None = None
    line: int = field(default=0, compare=False)

    @property
    def is_open(self) -> bool:
        return self.kind != "resume" and self.outcome == "open"

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "outcome": self.outcome,
            "timestamp": self.timestamp,
            "text": self.text,
        }


def validate_outcome(outcome: str) -> str:
    """Return *outcome* unchanged, or raise ValueError naming the valid forms."""
    if not _OUTCOME_RE.match(outcome):
        raise ValueError(
            f"invalid outcome {outcome!r}: use open, approved, dropped or 'filed <owner/repo#n>'"
        )
    return outcome


def utc_now() -> str:
    """Current time as the ISO 8601 UTC timestamp a resume line carries."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_date() -> str:
    """Current UTC date, as the archive name of a finished ledger carries it."""
    return datetime.now(UTC).strftime("%Y-%m-%d")


def format_entry(entry: LedgerEntry) -> str:
    """Render *entry* as one ledger line. Newlines in the text become spaces.

    A ``[logbook]`` line keeps its text verbatim: it is one line of JSON, and a
    retried delivery must send the entry text unchanged (issue #490).
    """
    if entry.kind not in KINDS:
        raise ValueError(f"invalid kind {entry.kind!r}: use one of {', '.join(KINDS)}")
    text = entry.text if entry.kind == "logbook" else " ".join(entry.text.split())
    if entry.kind == "resume":
        status = entry.timestamp or utc_now()
    elif entry.kind == "logbook":
        status = entry.outcome or ""
        if not _DELIVERY_RE.match(status):
            raise ValueError(
                f"invalid logbook delivery {status!r}: use '<sink>=delivered|undelivered'"
            )
    elif entry.kind == "board":
        status = entry.outcome or ""
        if not _URL_RE.match(status):
            raise ValueError(f"invalid board URL {status!r}: use the board comment's URL")
    else:
        status = validate_outcome(entry.outcome or "open")
    return f"- [{entry.kind}] {status}{SEP}{text}"


def parse_line(raw: str, lineno: int = 0) -> LedgerEntry | None:
    """Parse one line. Returns None for a line that is not a ledger entry."""
    stripped = raw.strip()
    if not stripped.startswith("- ["):
        return None
    match = _LINE_RE.match(stripped)
    where = f"line {lineno}: " if lineno else ""
    if not match:
        raise LedgerParseError(f"{where}malformed ledger line: {stripped}")
    kind, status, text = match["kind"], match["status"], match["text"].strip()
    if kind not in KINDS:
        raise LedgerParseError(f"{where}unknown ledger kind {kind!r}: {stripped}")
    if kind == "resume":
        if not _TIMESTAMP_RE.match(status):
            raise LedgerParseError(f"{where}resume line needs an ISO timestamp: {stripped}")
        return LedgerEntry(kind=kind, text=text, timestamp=status, line=lineno)
    pattern = {"logbook": _DELIVERY_RE, "board": _URL_RE}.get(kind, _OUTCOME_RE)
    if not pattern.match(status):
        raise LedgerParseError(f"{where}invalid outcome {status!r}: {stripped}")
    return LedgerEntry(kind=kind, text=text, outcome=status, line=lineno)


def parse_ledger(text: str, *, first_line: int = 1) -> list[LedgerEntry]:
    """Parse every ledger line in *text*; other lines are ignored."""
    entries: list[LedgerEntry] = []
    for offset, raw in enumerate(text.splitlines()):
        entry = parse_line(raw, first_line + offset)
        if entry is not None:
            entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

_FILE_HEADING = "# Lore ledger\n"


def append_entry(path: Path, entry: LedgerEntry) -> str:
    """Append *entry* to the ledger file at *path*; create it with a heading.

    Returns the line written.
    """
    line = format_entry(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else _FILE_HEADING + "\n"
    if existing and not existing.endswith("\n"):
        existing += "\n"
    path.write_text(existing + line + "\n", encoding="utf-8")
    return line


#: Kinds whose line carries no outcome for ``ledger-set``.
_NO_OUTCOME = ("resume", "logbook", "board")


def _select(entries: list[LedgerEntry], selector: str) -> LedgerEntry:
    """Pick one entry by 1-based index or by a case-insensitive text match."""
    if selector.isdigit():
        index = int(selector)
        if not 1 <= index <= len(entries):
            raise ValueError(f"no ledger line {index}: the ledger has {len(entries)}")
        return entries[index - 1]
    needle = selector.casefold()
    hits = [e for e in entries if e.kind not in _NO_OUTCOME and needle in e.text.casefold()]
    if len(hits) != 1:
        raise ValueError(f"{len(hits)} ledger lines match {selector!r}; expected exactly one")
    return hits[0]


def set_outcome(path: Path, selector: str, outcome: str) -> str:
    """Set the outcome of one non-resume line in the ledger file at *path*.

    *selector* is a 1-based entry index (as ``ledger-check`` lists them) or a
    case-insensitive substring that matches exactly one non-resume line.
    Every other line stays as it was. Returns the rewritten line.
    """
    text = path.read_text(encoding="utf-8")
    new_text, new_line = _rewrite_outcome(text, parse_ledger(text), selector, outcome)
    path.write_text(new_text, encoding="utf-8")
    return new_line


def set_board_outcome(text: str, selector: str, outcome: str) -> tuple[str, str]:
    """Set the outcome of one line in the ``## Ledger`` section of a board comment.

    *selector* works as in :func:`set_outcome`, counted over the board's
    ledger section only. Every other line of *text*, including a line with
    the same text outside the section, stays as it was. Returns the whole
    updated comment and the rewritten line.
    """
    return _rewrite_outcome(text, parse_board_ledger(text), selector, outcome)


def _rewrite_outcome(
    text: str, entries: list[LedgerEntry], selector: str, outcome: str
) -> tuple[str, str]:
    """Rewrite the source line of the selected entry; return (text, new line)."""
    validate_outcome(outcome)
    target = _select(entries, selector)
    if target.kind in _NO_OUTCOME:
        raise ValueError(f"a {target.kind} line carries no outcome")
    new_line = format_entry(LedgerEntry(kind=target.kind, text=target.text, outcome=outcome))
    lines = text.splitlines(keepends=True)
    ending = "\n" if lines[target.line - 1].endswith("\n") else ""
    lines[target.line - 1] = new_line + ending
    return "".join(lines), new_line


def archive_ledger(path: Path) -> Path:
    """Rename a finished ledger to ``lore-ledger.<UTC-date>.done.md`` beside it.

    A run archives its ledger at the finish point, after the ledger check
    passes, so the SessionStart resume offer stops firing for it. Raises
    ValueError while a line is still open. A second archive on the same day
    gets a ``-2``, ``-3`` … suffix and leaves the earlier one in place.
    Each ``[logbook]`` line with an undelivered sink is carried into a new
    ledger at *path*, so ``lore log flush`` still finds it. Returns the
    archive path.
    """
    entries = parse_ledger(path.read_text(encoding="utf-8"))
    still_open = open_entries(entries)
    if still_open:
        raise ValueError(
            f"{len(still_open)} open line(s); run `lore workflow ledger-check` and set each outcome"
        )
    stem = path.name.removesuffix(".md")
    date = utc_date()
    target = path.with_name(f"{stem}.{date}.done.md")
    n = 2
    while target.exists():
        target = path.with_name(f"{stem}.{date}-{n}.done.md")
        n += 1
    path.rename(target)
    undelivered = [
        format_entry(e)
        for e in entries
        if e.kind == "logbook" and "=undelivered" in (e.outcome or "")
    ]
    if undelivered:
        path.write_text(_FILE_HEADING + "\n" + "\n".join(undelivered) + "\n", encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# Locating ledgers
# ---------------------------------------------------------------------------


def find_git_dir(start: Path) -> Path | None:
    """Return what ``git rev-parse --git-dir`` names for *start*, without git.

    Walks up from *start* to the first ``.git``. A directory is the git dir.
    A file (linked worktree, submodule) holds ``gitdir: <path>``, relative to
    the file's folder when not absolute. Returns None outside a repo.
    """
    try:
        current = Path(start).resolve()
    except OSError:
        return None
    for folder in (current, *current.parents):
        dotgit = folder / ".git"
        if dotgit.is_dir():
            return dotgit
        if dotgit.is_file():
            try:
                content = dotgit.read_text(encoding="utf-8").strip()
            except OSError:
                return None
            if not content.startswith("gitdir:"):
                return None
            target = Path(content.partition(":")[2].strip())
            return target if target.is_absolute() else (folder / target).resolve()
    return None


def default_ledger_path(cwd: Path) -> Path | None:
    """The loop/issue ledger path for *cwd*, or None outside a repo."""
    git_dir = find_git_dir(cwd)
    return None if git_dir is None else git_dir / LEDGER_FILENAME


def parse_board_ledger(text: str) -> list[LedgerEntry]:
    """Parse the ``## Ledger`` section of an epic board comment.

    The section runs from its heading to the next Markdown heading or the
    end of the comment. A board without the section has an empty ledger.
    Line numbers count from the start of *text*.
    """
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.strip() == BOARD_LEDGER_HEADING), None)
    if start is None:
        return []
    end = next(
        (j for j in range(start + 1, len(lines)) if lines[j].lstrip().startswith("#")),
        len(lines),
    )
    return parse_ledger("\n".join(lines[start + 1 : end]), first_line=start + 2)


def parse_ledger_source(text: str) -> list[LedgerEntry]:
    """Parse a ledger file, or the ``## Ledger`` section of a board comment.

    Text that carries the board marker is a board comment; anything else is
    a ledger file.
    """
    from lore_workflow.board_parser import BOARD_MARKER

    if BOARD_MARKER in text:
        return parse_board_ledger(text)
    return parse_ledger(text)


def open_entries(entries: list[LedgerEntry]) -> list[tuple[int, LedgerEntry]]:
    """Return ``(1-based index, entry)`` for each non-resume line still open."""
    return [(i, e) for i, e in enumerate(entries, start=1) if e.is_open]


# ---------------------------------------------------------------------------
# Breakpoint hooks
# ---------------------------------------------------------------------------


def _tolerant_entries(text: str) -> list[LedgerEntry | None]:
    """Parse every line; a malformed one becomes None instead of raising."""
    entries: list[LedgerEntry | None] = []
    for raw in text.splitlines():
        try:
            entry = parse_line(raw)
        except LedgerParseError:
            entries.append(None)
            continue
        if entry is not None:
            entries.append(entry)
    return entries


def last_board_line(text: str) -> LedgerEntry | None:
    """The last well-formed board line in *text*, or None."""
    boards = [e for e in _tolerant_entries(text) if e is not None and e.kind == "board"]
    return boards[-1] if boards else None


def board_api_path(url: str) -> str | None:
    """``repos/<owner>/<repo>/issues/comments/<id>`` for a GitHub comment URL, or None."""
    match = re.match(
        r"^https://github\.com/([\w.-]+)/([\w.-]+)/(?:issues|pull)/\d+#issuecomment-(\d+)$", url
    )
    return f"repos/{match[1]}/{match[2]}/issues/comments/{match[3]}" if match else None


def _board_read(board: LedgerEntry) -> str:
    """How to read the board of *board*: a ``gh api`` pipe when the URL allows."""
    api = board_api_path(board.outcome or "")
    if api:
        return f"`gh api {api} --jq .body | lore workflow parse-board`"
    return "its comment body piped into `lore workflow parse-board`"


def precompact_note(cwd: Path) -> str | None:
    """The PreCompact lines that name the ledger of *cwd* and its epic board, or None."""
    path = default_ledger_path(cwd)
    if path is None or not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    entries = _tolerant_entries(text)
    kinds = {e.kind if e is not None else "malformed" for e in entries}
    notes: list[str] = []
    board = last_board_line(text)
    if board is not None:
        notes.append(
            f"lore: epic board for {board.text} at {board.outcome} — re-read it after "
            f"compaction with {_board_read(board)}; the board holds the epic's ledger."
        )
    if kinds - {"logbook", "board"}:  # logbook lines alone are no run (issue #490)
        notes.append(
            f"lore: build ledger at {path} — re-read it after compaction; "
            "`lore workflow ledger-check` gates the finish point."
        )
    return "\n".join(notes) or None


def last_resume_line(text: str) -> str | None:
    """Return the last well-formed resume line in *text*, verbatim.

    Tolerant by design: a malformed line elsewhere does not hide it.
    """
    found: str | None = None
    for raw in text.splitlines():
        if not raw.lstrip().startswith("- [resume]"):
            continue
        try:
            entry = parse_line(raw)
        except LedgerParseError:
            continue
        if entry is not None:
            found = raw.strip()
    return found


def _looks_finished(text: str) -> bool:
    """True when no line is open and the last entry is not a resume line.

    Tolerant like :func:`last_resume_line`: malformed lines are skipped.
    """
    entries = [e for e in _tolerant_entries(text) if e is not None and e.kind != "board"]
    if not entries or any(e.is_open for e in entries):
        return False
    return entries[-1].kind != "resume"


def resume_offer(cwd: Path) -> str | None:
    """The SessionStart line offering to resume, or None.

    A board line offers the epic run until the epic tail archives the ledger.
    Otherwise None without a resume line, and None for a ledger that looks
    finished: no open line, and an outcome line after the last resume line.
    The build skill archives a finished ledger (:func:`archive_ledger`), which
    also ends the offer.
    """
    path = default_ledger_path(cwd)
    if path is None or not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    board = last_board_line(text)
    if board is not None:
        return (
            f"lore: an epic run for {board.text} is in progress from this checkout; its board "
            f"is {board.outcome}. Offer the user to resume it with "
            f"`/lore-workflow:build epic {board.text}`. Read the board with {_board_read(board)}."
        )
    line = last_resume_line(text)
    if line is None or _looks_finished(text):
        return None
    return (
        f"lore: build ledger at {path} has a resume point. "
        f'Offer the user to resume from it: "{line}"'
    )
