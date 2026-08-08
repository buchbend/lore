"""The flag — one stamped, standing-alone fact crossing into the wiki.

A flag is the deliberate path from a working session to the team
surface: an agent files one team-relevant fact the moment it appears,
and Lore appends it to the owning topic note straight away, marked
unreviewed (``docs/adr/0008``). It is the only path: lore writes nothing
else into a wiki from a session. Nothing here composes, summarises or judges — the
caller supplies a lead sentence and a short body, and every word around
them is written by code.

The block Lore renders::

    <!-- lore:flag id=ab12cd34ef56 -->
    **Reported in session: the reaper starves mid-drain.**

    Two sessions raced the same lock; the loser never retried.

    _flag · claude · 2026-08-05 · pr 357 (unchecked) · transcript tr-9f2c · unreviewed_
    <!-- /lore:flag -->

The comment fence is what makes every review verdict exact: accept
rewrites one line, decline drops the fenced lines, retarget cuts them
out of one note and appends them to another. A human editing the prose
around a flag cannot make the fence ambiguous, which a heading-delimited
block could not promise.

Three rules the shape encodes:

* **No origin, no flag.** A write carrying neither a transcript pointer
  nor a single ref is refused — a fact with nothing behind it has no
  business on a shared surface.
* **Code owns the phrasing** (``docs/adr/0004``). Refs are verified at
  write time; a verified flag states itself and shows its pointer with a
  check mark, an unverifiable one is handed back to the session that said
  it, and a ref that does not exist demotes the whole line. A
  human-authored flag skips the stamping — a person owns their own words
  — and lands without the unreviewed marker.
* **Pending state is derived, never stored.** The unreviewed token at the
  end of the origin line is the single machine-readable pending signal;
  :func:`pending` finds flags by scanning notes for it. No queue store
  exists to drift out of sync with the wiki (``docs/adr/0008``).

No LLM runs anywhere in this module.
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from lore_core import quarantine as _quarantine
from lore_core.io import atomic_write_text
from lore_core.publish_gate import CATEGORY_ERROR, Detector, evaluate
from lore_core.ref_verify import MISSING, UNCHECKED, VERIFIED, verify_refs
from lore_core.spine import SpineWriter

__all__ = [
    "BLOCK_CLOSE",
    "BLOCK_OPEN_PREFIX",
    "EV_REVIEW",
    "EV_WRITE",
    "LEAD_MISSING",
    "LEAD_UNCHECKED",
    "ORIGIN_PREFIX",
    "SPINE_SOURCE",
    "UNREVIEWED_TOKEN",
    "FlagWrite",
    "OriginMissing",
    "PendingFlag",
    "accept",
    "count_pending",
    "decline",
    "pending",
    "propose_target",
    "retarget",
    "write",
]

# Block fence. The id is what a review verdict addresses; it is opaque and
# carries no meaning beyond identity.
BLOCK_OPEN_PREFIX = "<!-- lore:flag id="
BLOCK_CLOSE = "<!-- /lore:flag -->"
_OPEN_RE = re.compile(r"^<!-- lore:flag id=([0-9a-f]{12}) -->$")

# Origin line. Starts with the prefix, ends with the unreviewed token until a
# human reviews the flag.
ORIGIN_PREFIX = "_flag · "
UNREVIEWED_TOKEN = "unreviewed"
_MARKER_SUFFIX = f" · {UNREVIEWED_TOKEN}"

# Where a flag lands when no home note exists. Topic notes are concepts;
# projects/decisions/papers are authored deliberately, not by a crossing.
_DEFAULT_DIR = "concepts"

SPINE_SOURCE = "flag"
EV_WRITE = "flag-write"
EV_REVIEW = "flag-review"

# Code-owned leads (docs/adr/0004). A flag whose refs could not be checked is
# handed back to the session that said it; one whose ref does not exist is
# demoted further. A verified flag needs no lead — its pointer carries the load.
LEAD_UNCHECKED = "Reported in session:"
LEAD_MISSING = "Claimed in session, ref not found:"
_STAMPS = {VERIFIED: "✓", UNCHECKED: "(unchecked)", MISSING: "(not found)"}

_SLUG_MAX = 48


class OriginMissing(ValueError):
    """Raised when a write carries neither a transcript pointer nor a ref."""


@dataclass(frozen=True)
class FlagWrite:
    """Outcome of one :func:`write`.

    ``status`` is ``"written"`` or ``"withheld"``. On a withhold the note
    is untouched, ``quarantine_id`` names the sidecar entry holding the
    refused text, and ``category`` names what tripped the gate.
    """

    status: str
    flag_id: str
    note_path: str
    created_note: bool = False
    reviewed: bool = False
    quarantine_id: str = ""
    category: str = ""


@dataclass(frozen=True)
class PendingFlag:
    """One unreviewed flag found by scanning a wiki."""

    id: str
    note_path: str
    lead: str
    origin: str
    block: str


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def _neutralize(text: str) -> str:
    """Defuse the two tokens caller text could forge.

    A lead or body comes from a transcript — model output, file contents,
    tool results — so either can carry a literal ``<!-- /lore:flag -->``
    or a line that opens like an origin line. Rendered raw, the first
    closes the fence early and leaves the rest of the flag loose in the
    note; the second gives the block a second origin line. Escaping the
    comment OPENER and the leading origin token kills both: nothing but a
    line this module wrote can open a fence or claim an origin.
    """
    text = text.replace("<!--", "&lt;!--")
    return "\n".join(
        "\\" + line if line.startswith(ORIGIN_PREFIX) else line for line in text.split("\n")
    )


def _one_line(text: str) -> str:
    return " ".join(text.split())


def slugify(text: str) -> str:
    """Filename slug for a new topic note, capped on a word boundary."""
    words = re.sub(r"[^a-z0-9]+", " ", text.lower()).split()
    slug = ""
    for word in words:
        candidate = f"{slug}-{word}" if slug else word
        if len(candidate) > _SLUG_MAX:
            break
        slug = candidate
    return slug or "flag"


def _titleize(slug: str) -> str:
    words = slug.replace("-", " ").strip()
    return words[:1].upper() + words[1:] if words else "Flag"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _weakest(refs: Sequence[tuple[str, str]], verdicts: dict[tuple[str, str], str]) -> str:
    """The flag's verification: its weakest ref, ``UNCHECKED`` when it has none.

    A ref the verifier said nothing about counts as unchecked — absence of
    a verdict is never a pass, so a skipped or crashed verification
    degrades the phrasing instead of forging a check mark.
    """
    if not refs:
        return UNCHECKED
    seen = [verdicts.get((t, v), UNCHECKED) for t, v in refs]
    for verdict in (MISSING, UNCHECKED):
        if verdict in seen:
            return verdict
    return VERIFIED


def _ref_clause(refs: Sequence[tuple[str, str]], verdicts: dict[tuple[str, str], str]) -> str:
    parts = []
    for ref_type, value in refs:
        stamp = _STAMPS[verdicts.get((ref_type, value), UNCHECKED)]
        parts.append(f"{_one_line(ref_type)} {_neutralize(_one_line(value))} {stamp}")
    return ", ".join(parts)


def render_block(
    *,
    flag_id: str,
    lead: str,
    body: str,
    author: str,
    day: str,
    refs: Sequence[tuple[str, str]],
    verdicts: dict[tuple[str, str], str],
    transcript: str,
    reviewed: bool,
    stamped: bool,
) -> str:
    """Render one flag block. Pure — same inputs, same bytes, always.

    ``stamped`` selects the code-owned lead template (agent-authored
    flags). A human-authored flag renders its lead verbatim: ADR 0004
    constrains what a *model* may claim, not what a person writes.
    """
    headline = _neutralize(_one_line(lead))
    if stamped:
        verdict = _weakest(refs, verdicts)
        if verdict == MISSING:
            headline = f"{LEAD_MISSING} {headline}"
        elif verdict == UNCHECKED:
            headline = f"{LEAD_UNCHECKED} {headline}"

    parts = [f"**{headline}**"]
    body_text = _neutralize(body.strip())
    if body_text:
        parts.append(body_text)

    origin = f"{ORIGIN_PREFIX}{_one_line(author)} · {day}"
    clause = _ref_clause(refs, verdicts)
    if clause:
        origin += f" · {clause}"
    if transcript:
        origin += f" · transcript {_neutralize(_one_line(transcript))}"
    if not reviewed:
        origin += _MARKER_SUFFIX
    parts.append(f"{origin}_")

    fenced = "\n\n".join(parts)
    return f"{BLOCK_OPEN_PREFIX}{flag_id} -->\n{fenced}\n{BLOCK_CLOSE}"


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def propose_target(wiki_path: Path, query: str) -> Path | None:
    """Top-ranked existing note for ``query``, or ``None`` when nothing fits.

    Route-before-write: the caller names a target or Lore proposes one,
    and only a wiki with no plausible home gets a new note. Ranking is the
    ordinary search backend — a flag should land where a reader searching
    the same words would look.

    Reindexes the wiki first, because a flag is often filed minutes after
    the note it belongs to was written. Incremental (sha-keyed), and the
    whole call degrades to ``None`` on any backend failure: a missing
    index costs a new topic note, never a lost flag.
    """
    try:
        from lore_search.fts import FtsBackend

        backend = FtsBackend()
        backend.reindex(wiki=wiki_path.name)
        hits = backend.search(query, wiki=wiki_path.name, k=1)
    except Exception:  # noqa: BLE001 — routing must never fail a write
        return None
    for hit in hits:
        candidate = wiki_path / hit.path
        if candidate.is_file() and candidate.parent.name != "sessions":
            return candidate
    return None


def _confine(wiki_path: Path, path: Path) -> Path:
    """Refuse a path that leaves the wiki.

    A target is a model-authored string, so ``../../../etc/passwd.md`` is
    a value the write path must expect. Wikis are portable units and a
    flag belongs to exactly one of them, so anything resolving outside is
    refused rather than clamped.
    """
    root = wiki_path.resolve()
    if not path.resolve().is_relative_to(root):
        raise ValueError(f"flag target must stay inside the wiki: {path}")
    return path


def _named_target(wiki_path: Path, target: str) -> Path:
    """Resolve a caller-named target: a wiki-relative path or a bare slug."""
    target = target.strip()
    if target.startswith("/"):
        raise ValueError(f"flag target must be wiki-relative: {target}")
    if target.endswith(".md"):
        return _confine(wiki_path, wiki_path / target)
    for existing in _note_files(wiki_path):
        if existing.stem == target:
            return existing
    return _confine(wiki_path, wiki_path / _DEFAULT_DIR / f"{target}.md")


def _proposed_target(wiki_path: Path, lead: str) -> Path:
    """Where an unnamed flag lands. Runs a search, so the gate goes first."""
    proposed = propose_target(wiki_path, lead)
    if proposed is not None:
        return proposed
    return wiki_path / _DEFAULT_DIR / f"{slugify(lead)}.md"


def _wiki_path(wiki: str | None, cwd: Path | None) -> Path:
    from lore_core.config import get_wiki_root

    root = get_wiki_root()
    if wiki:
        path = root / wiki
        if path.parent.resolve() != root.resolve():
            raise ValueError(f"not a wiki name: {wiki!r}")
        return path
    from lore_core.scope_resolver import resolve_scope

    scope = resolve_scope(cwd or Path.cwd())
    if scope is None:
        raise ValueError("no wiki resolved — pass a wiki name or run inside an attached repo")
    return root / scope.wiki


# ---------------------------------------------------------------------------
# Note I/O
# ---------------------------------------------------------------------------


def _note_files(wiki_path: Path) -> list[Path]:
    from lore_core.lint import discover_notes

    if not wiki_path.is_dir():
        return []
    return discover_notes(wiki_path)


def _append_block(path: Path, block: str, *, description: str, day: str) -> bool:
    """Append ``block`` to ``path``, creating the topic note if absent.

    Returns whether the note was created. Agents append and never edit
    (ADR 0008): the existing text is copied through unread, including a
    human's own prose around earlier flags. Not byte-for-byte — a CRLF
    note comes back LF and trailing blank lines are dropped, both of
    which ``read_text`` and ``rstrip`` do on the way through.
    """
    if path.exists():
        existing = path.read_text(encoding="utf-8").rstrip("\n")
        atomic_write_text(path, f"{existing}\n\n{block}\n")
        return False

    slug = path.stem
    frontmatter = {
        "schema_version": 2,
        "type": "concept",
        "created": day,
        "last_reviewed": day,
        "description": description,
        "tags": [],
    }
    dumped = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, f"---\n{dumped}\n---\n\n# {_titleize(slug)}\n\n{block}\n")
    return True


def _commit(wiki_path: Path, note_path: Path, flag_id: str) -> bool:
    """Commit the flag into the wiki repo. Best-effort; never raises.

    Three parts carry a flag to a teammate: this write, this commit, and
    the push the session boundary runs. A wiki that is not a git repo
    keeps the flag in its working tree instead — a transport that is not
    there must never cost the write.
    """
    from lore_core.session import commit_note

    try:
        ok, _detail = commit_note(
            wiki_path=wiki_path,
            note_path=note_path,
            message=f"lore: flag {flag_id}",
        )
    except (OSError, ValueError):
        return False
    return ok


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------


def _emit(lore_root: Path, event: str, wiki: str, **data) -> None:
    """One spine record per flag write and per review verdict.

    Never carries flag text: a withheld flag's telemetry must not restate
    what the gate refused to publish.
    """
    SpineWriter(lore_root).emit(source=SPINE_SOURCE, event=event, wiki=wiki, data=data)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def write(
    lead: str,
    body: str = "",
    *,
    wiki: str | None = None,
    target: str | None = None,
    refs: Sequence[tuple[str, str]] = (),
    transcript: str | None = None,
    author: str | None = None,
    human: bool = False,
    cwd: Path | None = None,
    lore_root: Path | None = None,
    repo_root: Path | None = None,
    repo: str = "",
    detector: Detector | None = None,
    now: str | None = None,
) -> FlagWrite:
    """File one flag. Deterministic; no LLM, no pipeline.

    Raises :class:`OriginMissing` when the write carries no origin data and
    :class:`ValueError` on an empty lead. Everything else — a tripped
    gate, an unroutable wiki, an unverifiable ref — is reported in the
    returned :class:`FlagWrite` rather than raised.
    """
    from lore_core.config import get_lore_root

    lead = _one_line(lead)
    if not lead:
        raise ValueError("flag lead must be non-empty")

    refs = [(str(t).strip(), str(v).strip()) for t, v in refs if str(v).strip()]
    transcript = (transcript or os.environ.get("CLAUDE_SESSION_ID") or "").strip()
    if not transcript and not refs:
        raise OriginMissing("a flag needs an origin: pass a transcript pointer or at least one ref")

    day = now or date.today().isoformat()
    if author is None:
        from lore_core import journal

        author = journal.default_author("human" if human else "ai")

    root = lore_root or get_lore_root()
    wiki_path = _wiki_path(wiki, cwd)
    # Resolving a NAMED target reads only the caller's own string and the
    # wiki's filenames. Proposing one runs a search, and the search backend
    # persists its query — so that waits until the gate has cleared the text.
    named = _named_target(wiki_path, target) if target else None
    flag_id = uuid.uuid4().hex[:12]

    # The gate scans what a reader would read — never the structural
    # markers around it — and fails closed (docs/adr/0008). It runs before
    # anything else touches the text.
    gate_text = "\n".join([lead, body.strip(), *(v for _t, v in refs)]).strip()
    verdict = evaluate(gate_text, detector=detector)
    if not verdict.passed:
        category = verdict.category or CATEGORY_ERROR
        entry = _quarantine.add_entry(
            category=category,
            note_path=str(named or wiki_path),
            # A flag is a standing-alone fact, not a slice of a transcript:
            # the turn range the quarantine sidecar records for withheld
            # chapters has no counterpart here.
            from_turn=0,
            to_turn=0,
            composed_text=gate_text,
            lore_root=root,
        )
        _emit(
            root,
            EV_WRITE,
            wiki_path.name,
            outcome="withheld",
            category=category,
            flag_id=flag_id,
        )
        return FlagWrite(
            status="withheld",
            flag_id=flag_id,
            note_path=str(named or wiki_path),
            quarantine_id=entry.id,
            category=category,
        )

    note_path = named or _proposed_target(wiki_path, lead)
    verdicts = verify_refs(refs, repo_root=repo_root, repo=repo)
    block = render_block(
        flag_id=flag_id,
        lead=lead,
        body=body,
        author=author,
        day=day,
        refs=refs,
        verdicts=verdicts,
        transcript=transcript,
        reviewed=human,
        stamped=not human,
    )
    created = _append_block(note_path, block, description=lead, day=day)
    committed = _commit(wiki_path, note_path, flag_id)
    _emit(
        root,
        EV_WRITE,
        wiki_path.name,
        outcome="written",
        flag_id=flag_id,
        note=str(note_path.relative_to(wiki_path)),
        created_note=created,
        reviewed=human,
        committed=committed,
    )
    return FlagWrite(
        status="written",
        flag_id=flag_id,
        note_path=str(note_path),
        created_note=created,
        reviewed=human,
    )


# ---------------------------------------------------------------------------
# Scanning — pending state is derived, never stored (docs/adr/0008)
# ---------------------------------------------------------------------------


def _bare(line: str) -> str:
    """The line without the CR a CRLF file carries.

    Notes are split on ``"\n"`` and rejoined with it, so a verdict cannot
    rewrite a U+2028/U+2029/U+0085/form-feed that ``str.splitlines`` would
    have read as a line break — those live inside a human's own prose and
    are never ours to edit. Matching then has to ignore the CR itself.
    """
    return line[:-1] if line.endswith("\r") else line


def _read(path: Path) -> list[str]:
    """Read a note into lines, keeping the bytes a verdict must not change.

    ``newline=""`` turns off universal-newline translation, so a CRLF note
    survives a verdict as CRLF instead of being silently converted.
    """
    with path.open(encoding="utf-8", newline="") as fh:
        return fh.read().split("\n")


def _spans(lines: list[str]) -> list[tuple[str, int, int]]:
    """``(flag_id, first_line, last_line)`` for every fenced block, in order."""
    spans: list[tuple[str, int, int]] = []
    start: int | None = None
    flag_id = ""
    for i, line in enumerate(lines):
        bare = _bare(line)
        match = _OPEN_RE.match(bare)
        if match:
            start, flag_id = i, match.group(1)
        elif bare == BLOCK_CLOSE and start is not None:
            spans.append((flag_id, start, i))
            start, flag_id = None, ""
    return spans


def _origin_index(lines: list[str]) -> int:
    """Index of the block's origin line — the LAST candidate, or ``-1``.

    ``render_block`` emits the origin line last, so a body line that opens
    like one (in a note written before the escape landed, or edited by
    hand) sits above it and must not be read instead.
    """
    for i in range(len(lines) - 1, -1, -1):
        if _bare(lines[i]).startswith(ORIGIN_PREFIX):
            return i
    return -1


def _block_to_flag(path: Path, flag_id: str, lines: list[str]) -> PendingFlag:
    lead = next((_bare(line).strip("* ") for line in lines if line.startswith("**")), "")
    origin_at = _origin_index(lines)
    return PendingFlag(
        id=flag_id,
        note_path=str(path),
        lead=lead,
        origin=_bare(lines[origin_at]) if origin_at >= 0 else "",
        block="\n".join(_bare(line) for line in lines),
    )


def _is_unreviewed(origin: str) -> bool:
    return origin.endswith(f"{_MARKER_SUFFIX}_")


def flags_in(path: Path) -> list[PendingFlag]:
    """Every flag block in one note, reviewed or not, in file order."""
    try:
        lines = _read(path)
    except (OSError, UnicodeDecodeError):
        return []
    return [_block_to_flag(path, fid, lines[start : end + 1]) for fid, start, end in _spans(lines)]


def pending(wiki_path: Path) -> list[PendingFlag]:
    """Every unreviewed flag in ``wiki_path``, note by note.

    Walks the wiki rather than consulting an index: the marker in the note
    is the only pending signal that exists, so a note a human edited by
    hand is read exactly like one Lore wrote.
    """
    out: list[PendingFlag] = []
    for path in _note_files(wiki_path):
        out.extend(f for f in flags_in(path) if _is_unreviewed(f.origin))
    return out


def count_pending(wiki_path: Path) -> int:
    """Number of unreviewed flags — the only thing the banner may show.

    Derived from the same scan the review walk uses, deliberately: a
    count from a second, looser scan would nudge toward flags the walk
    never presents.

    Full-wiki scan, no cache. At a few hundred notes this is one cheap
    read each; if it ever shows up in a SessionStart profile, the count
    belongs in the wiki catalog alongside the pending-verdict count.
    """
    return len(pending(wiki_path))


# ---------------------------------------------------------------------------
# Review verdicts
# ---------------------------------------------------------------------------


def _locate(wiki_path: Path, flag_id: str) -> tuple[Path, list[str], int, int] | None:
    for path in _note_files(wiki_path):
        try:
            lines = _read(path)
        except (OSError, UnicodeDecodeError):
            continue
        for fid, start, end in _spans(lines):
            if fid == flag_id:
                return path, lines, start, end
    return None


def _cut(lines: list[str], start: int, end: int) -> list[str]:
    """Drop a block plus the blank line that separated it from its neighbour."""
    head = lines[:start]
    while head and not head[-1].strip():
        head.pop()
    return head + lines[end + 1 :]


def _write_lines(path: Path, lines: list[str]) -> None:
    """Rejoin with the separator the file was split on — nothing else."""
    atomic_write_text(path, "\n".join(lines))


def _lore_root_for(wiki_path: Path) -> Path:
    """``<lore_root>/wiki/<name>`` → ``<lore_root>``."""
    return wiki_path.parent.parent


def accept(wiki_path: Path, flag_id: str) -> bool:
    """Remove the unreviewed marker and change nothing else in the note."""
    found = _locate(wiki_path, flag_id)
    if found is None:
        return False
    path, lines, start, end = found
    origin_at = start + _origin_index(lines[start : end + 1])
    if origin_at >= start:
        bare = _bare(lines[origin_at])
        if _is_unreviewed(bare):
            # Slice off the marker and put back whatever ended the line.
            lines[origin_at] = (
                bare[: -len(f"{_MARKER_SUFFIX}_")] + "_" + lines[origin_at][len(bare) :]
            )
    _write_lines(path, lines)
    _emit(
        _lore_root_for(wiki_path),
        EV_REVIEW,
        wiki_path.name,
        verdict="accept",
        flag_id=flag_id,
    )
    return True


def decline(wiki_path: Path, flag_id: str) -> bool:
    """Delete the flag block. The note's own prose is left untouched."""
    found = _locate(wiki_path, flag_id)
    if found is None:
        return False
    path, lines, start, end = found
    _write_lines(path, _cut(lines, start, end))
    _emit(
        _lore_root_for(wiki_path),
        EV_REVIEW,
        wiki_path.name,
        verdict="decline",
        flag_id=flag_id,
    )
    return True


def retarget(wiki_path: Path, flag_id: str, target: str) -> str:
    """Move the block to ``target``, creating that note when it is missing.

    A retarget corrects where a flag lives, not whether its text is
    endorsed, so the block keeps its unreviewed marker and stays in the
    review walk until someone accepts it (ADR 0008 gives marker removal to
    accept alone).
    """
    found = _locate(wiki_path, flag_id)
    if found is None:
        return ""
    path, lines, start, end = found
    block = "\n".join(lines[start : end + 1])
    destination = _named_target(wiki_path, target)
    if destination == path:
        return str(path)

    _write_lines(path, _cut(lines, start, end))
    lead = next(
        (_bare(line).strip("* ") for line in lines[start : end + 1] if line.startswith("**")),
        "",
    )
    _append_block(destination, block, description=lead, day=date.today().isoformat())
    _emit(
        _lore_root_for(wiki_path),
        EV_REVIEW,
        wiki_path.name,
        verdict="retarget",
        flag_id=flag_id,
        note=str(destination.relative_to(wiki_path)),
    )
    return str(destination)
