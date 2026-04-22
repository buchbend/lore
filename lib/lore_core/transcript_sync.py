"""Mirror Claude Code transcript JSONLs into each wiki's ``.transcripts/``.

Privacy boundary: the originals under ``~/.claude/projects/...`` are the
user's property, never pushed anywhere. The wiki-local mirror at
``<wiki>/.transcripts/<uuid>.jsonl`` is gitignored so distributed wikis
only transport curator-redacted summaries. The mirror enables
``lore transcripts show`` and any future "restore full context" workflow
without exposing raw transcripts to the wiki's backend.

Design constraints:
  * Atomic writes — a crash mid-copy must never leave a half-written
    file where a reader could parse past the end-of-file and trip on a
    partial JSON line. We write to ``<uuid>.jsonl.tmp``, validate the
    last line parses as JSON (truncating to the last newline if not),
    then ``os.replace``.
  * ``.gitignore`` is updated **before** the first copy. Opening that
    race window (mirror files staged before ignore line lands) would
    leak raw transcripts to git on the next commit.
  * Line-equality match on ``.gitignore`` entries — substring matches
    against patterns like ``.transcripts_backup`` would silently miss,
    and we must abort on any ``!`` negation touching our path (user is
    intentionally keeping transcripts tracked).
  * Own spawn lock (``role="transcripts"``) so concurrent session-start
    hooks across Claude sessions don't pile up sync work.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from lore_core.io import atomic_write_text
from lore_core.ledger import TranscriptLedger
from lore_core.scope_resolver import resolve_scope
from lore_core.state.attachments import AttachmentsFile


# Line-equality set for ``.gitignore`` — we treat these as "already
# covers .transcripts". Anything else is a no-match and triggers append.
_IGNORE_TOKENS: frozenset[str] = frozenset(
    {
        ".transcripts",
        ".transcripts/",
        ".transcripts/*",
        "/.transcripts",
        "/.transcripts/",
    }
)


@dataclass
class SyncResult:
    """Per-invocation outcome of :func:`sync_transcripts`."""

    copied: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


class GitignoreNegationError(RuntimeError):
    """Raised when ``.gitignore`` contains a ``!`` pattern touching ``.transcripts``.

    The user is explicitly keeping transcripts tracked. We must not
    silently override that intent.
    """


def _ensure_transcripts_gitignored(wiki_dir: Path) -> None:
    """Make sure ``<wiki_dir>/.gitignore`` ignores ``.transcripts/``.

    Idempotent. Reads the file line-by-line and compares stripped lines
    against :data:`_IGNORE_TOKENS`. Aborts if any ``!`` negation line
    contains ``.transcripts`` — the user is intentionally tracking
    transcripts and we must not override that.

    Adds a missing trailing newline before the appended line.
    """
    wiki_dir.mkdir(parents=True, exist_ok=True)
    path = wiki_dir / ".gitignore"
    if path.exists():
        try:
            text = path.read_text()
        except OSError as exc:
            raise RuntimeError(f"cannot read {path}: {exc}") from exc
    else:
        text = ""

    # Two passes: (1) fail hard if the user is intentionally tracking
    # transcripts via a ``!`` rule; (2) only then short-circuit on an
    # existing ignore line. Reversing this order would let a file with
    # ``.transcripts/`` on line 1 and ``!.transcripts/keep.jsonl`` on
    # line 2 return quietly, silently ignoring the user's negation.
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("!") and ".transcripts" in stripped:
            raise GitignoreNegationError(
                f"{path} has a negation rule touching .transcripts; "
                "refusing to silently override the user's intent"
            )

    for raw_line in text.splitlines():
        if raw_line.strip() in _IGNORE_TOKENS:
            return  # already ignored

    # Append. Preserve existing content; add a trailing newline if missing.
    if text and not text.endswith("\n"):
        text += "\n"
    text += ".transcripts/\n"
    atomic_write_text(path, text)


def _copy_transcript_atomically(src: Path, dst: Path) -> None:
    """Copy ``src`` to ``dst`` atomically; validate last line parses as JSON.

    Writes to ``<dst>.tmp`` first. If the last non-empty line doesn't
    parse as JSON (common when the source is mid-write), truncates back
    to the last successful newline. ``os.replace`` provides the atomic
    swap.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")

    try:
        shutil.copyfile(src, tmp)
    except OSError:
        # best-effort cleanup
        try:
            tmp.unlink()
        except OSError:
            pass
        raise

    # Validate & heal the tail. We only care about the LAST line — earlier
    # lines are independent JSON events and a mid-stream corruption is
    # extremely unlikely; the dominant failure mode is "source was being
    # written when we read" which leaves a partial final line.
    try:
        with tmp.open("rb+") as fp:
            fp.seek(0, os.SEEK_END)
            size = fp.tell()
            if size == 0:
                pass  # empty file is a valid state
            else:
                # Read back to find last '\n'
                fp.seek(max(0, size - 64 * 1024))
                tail = fp.read()
                last_nl = tail.rfind(b"\n")
                if last_nl == -1:
                    # No newline at all — drop the whole tail region
                    fp.truncate(0)
                else:
                    # Check the final line after the last newline
                    final = tail[last_nl + 1 :]
                    if final.strip():
                        try:
                            json.loads(final)
                        except json.JSONDecodeError:
                            # Truncate to last good newline (inclusive).
                            end = size - (len(tail) - last_nl - 1)
                            fp.truncate(end)
                    # Else: file ended on a newline — clean.
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise

    os.replace(tmp, dst)


def _needs_copy(src: Path, dst: Path) -> bool:
    """True when ``dst`` is missing or older than ``src``."""
    if not dst.exists():
        return True
    try:
        src_mtime = src.stat().st_mtime
        dst_mtime = dst.stat().st_mtime
    except OSError:
        return True
    return src_mtime > dst_mtime


def sync_transcripts(
    lore_root: Path,
    *,
    wiki: str | None = None,
) -> SyncResult:
    """Mirror each ledger transcript into its wiki's ``.transcripts/``.

    Walks the ``TranscriptLedger`` entries, resolves each entry's source
    cwd to a wiki via ``resolve_scope``, and copies the transcript file
    into ``<lore_root>/wiki/<wiki>/.transcripts/<uuid>.jsonl``. Ensures
    the wiki has ``.transcripts/`` in its ``.gitignore`` before any
    copy so raw transcripts can never race into a staged commit.

    Idempotent: skips entries whose destination is up-to-date.

    ``wiki``: restrict sync to that wiki name only.

    Returns a :class:`SyncResult` with copied/skipped counts and a list
    of per-entry error strings (never raises beyond the first failure —
    one bad source shouldn't block the rest).
    """
    ledger = TranscriptLedger(lore_root)
    attachments = AttachmentsFile(lore_root)
    attachments.load()

    result = SyncResult()

    for raw in ledger._load().values():
        try:
            entry = TranscriptLedger._entry_from_raw(raw)
        except (KeyError, ValueError) as exc:
            result.errors.append(f"malformed ledger entry: {exc}")
            continue

        if entry.orphan:
            result.skipped += 1
            continue

        src = entry.path
        if not src.exists():
            # Source gone (user deleted or reorganized). Skip silently —
            # `lore runs list --hooks` surfaces broader ledger hygiene.
            result.skipped += 1
            continue

        if not entry.directory.exists():
            # Source dir retired. Curator A marks orphan on its next pass;
            # for now, don't try to resolve a wiki that can't answer.
            result.skipped += 1
            continue

        scope = resolve_scope(entry.directory, attachments=attachments)
        if scope is None:
            result.skipped += 1
            continue

        if wiki is not None and scope.wiki != wiki:
            result.skipped += 1
            continue

        wiki_dir = lore_root / "wiki" / scope.wiki
        try:
            _ensure_transcripts_gitignored(wiki_dir)
        except GitignoreNegationError as exc:
            result.errors.append(f"{scope.wiki}: {exc}")
            continue
        except OSError as exc:
            result.errors.append(f"{scope.wiki}: gitignore update: {exc}")
            continue

        dst = wiki_dir / ".transcripts" / f"{entry.transcript_id}.jsonl"
        if not _needs_copy(src, dst):
            result.skipped += 1
            continue

        try:
            _copy_transcript_atomically(src, dst)
            result.copied += 1
        except OSError as exc:
            result.errors.append(f"{entry.transcript_id}: {exc}")

    return result
