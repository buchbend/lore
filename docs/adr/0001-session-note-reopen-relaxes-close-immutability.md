# ADR 0001: Session-note reopen relaxes close-immutability (one file per session)

- Status: Superseded by ADR 0007
- Date: 2026-07-06
- Context: epic [#151](https://github.com/buchbend/lore/issues/151),
  PRD [0002](../prd/0002-useful-session-notes.md), sub-issue
  [#148](https://github.com/buchbend/lore/issues/148)

## Context

A session note is the deterministic core's one-note-per-session record: a
fixed disclaimer followed by chronological chapters, one per flush. PRD 0001
established the lifecycle *create → append → (marker) → close*, where **close**
sets `note_status: closed` and the file becomes immutable — no further chapter
may be appended (`append_chapter` raises `NoteClosedError`).

That invariant assumed close is terminal for a session. It is not. A session's
buffer can be closed and archived to `_done/` while the session is still live
or about to resume:

- a **false liveness reap** — the owner `pid` is re-stamped every heartbeat to
  the ephemeral hook subprocess, which exits within milliseconds, so
  "pid gone" is the steady state, not death (fixed for the instant-reap case in
  #146, but a genuinely idle-then-resumed session still closes via staleness);
- a **genuine close followed by a resume** — an editor restart, a `/compact`,
  or an idle-then-return.

On resume the next heartbeat finds no live sidecar (it was archived), mints a
fresh one with an empty `stub_path`, and `ensure_note` creates a **second**
note file that re-narrates the first from an empty note-so-far. The pilot
(transcript `a737ff12`, 2026-07-04) split one session into `04-0605` (turns
1095–1383) and `04-0608` (turns 1384–1430): two unlinked files, the second
repeating the first. This defeats the product promise of one faithful,
skimmable record per session.

The fix requires appending a new chapter to the already-closed note — which the
immutability invariant forbids.

## Decision

Introduce a `reopen_note` primitive in the deterministic core
(`lore_core/note_document.py`) that flips a closed note's `note_status` back to
`open` (idempotent on an already-open note) and **deliberately relaxes the
close-immutability invariant for session notes only**, under a *one file per
session (reopen)* model:

- When a resumed session's heartbeat finds no live buffer for its stem
  (`<transcript_id>__<local_date>`), it first looks in `_done/` for its own
  archived buffer. If found, it **restores** that buffer (moves it back to the
  live dir, resets it to a fresh accumulation cycle preserving the note
  pointer), **reopens** the note, and continues appending chapters to the same
  file — seeding note-so-far from the existing body so the composer does not
  duplicate earlier content.
- The archive is *moved*, not copied, preserving the "one buffer per stem"
  invariant: a later close archives cleanly with no `_done/` collision.
- Reopen is unconditional for a closed session note. The note records **no
  close reason**, so there is no basis to distinguish "closed at a session
  boundary" from any other close; we do not invent one (YAGNI). Only a
  session's own continuing heartbeat, keyed on the identical stem, ever reopens
  its own note.

Immutability is preserved everywhere else: derived and curated artifacts
(concepts, decisions, threads) remain immutable, and no cross-session or
curator writer may reopen a session note. The carve-out is narrow — a session
may reopen *its own* note, nothing more.

## Consequences / Trade-offs

- **Positive.** One session yields exactly one note across false reaps and
  resumes; no duplicated sibling files; the continuation is seeded with the
  prior body so it reads as a single coherent record. The `04-0605`/`04-0608`
  split no longer occurs.
- **Negative — a closed note is no longer a hard-frozen artifact.** A reader or
  downstream consumer can no longer assume `note_status: closed` is permanent;
  the same file may gain chapters later if its session resumes. This is
  acceptable because a session note is explicitly a lab-notebook record (per
  its own disclaimer), not a source of truth, and the reopen only ever *appends*
  — earlier chapters are never edited, matching the existing append-only-until-
  close contract within a session.
- **Concurrency.** Two heartbeats racing to reattach the same archived buffer
  serialise on the per-stem flock (its `.lock` file is never archived, so the
  lock path is stable across archive/restore). The first restores and reopens;
  the second re-reads the now-live sidecar and takes the normal accumulate path.
  This reuses the exact serialisation the existing fresh-mint path already
  relies on.
- **Discarded prior note.** If the prior session was trivial/empty and its note
  file was deleted, the archived `stub_path` dangles. Restore detects the
  missing file, clears `stub_path`, and lets a fresh note be created for the
  resumed session rather than crashing on a reopen of a deleted file.

## Alternatives considered

- **A linked chain of sibling notes** (keep immutability; link `04-0608` back to
  `04-0605` via frontmatter). Rejected: it still fragments one session across
  files, still risks cross-file duplication, and complicates every reader and
  briefing-gather with chain-following. One file is simpler and matches the
  product promise directly.
- **A stable session-level owner id** replacing the ephemeral pid, so the buffer
  is never closed for a live session in the first place. Deferred (out of scope
  for this epic, PRD 0002): it is a deeper owner-identity redesign; routing
  pid-gone through the staleness path (#146) plus reopen-on-resume is sufficient
  now.
- **A close-reason gate on reopen** (only reopen notes closed "at a boundary").
  Rejected: no close reason is recorded today, so the distinction does not
  exist; inventing one would be speculative complexity.
