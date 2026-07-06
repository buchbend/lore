---
title: Session notes that carry the essence, one per session
status: draft
epic: https://github.com/buchbend/lore/issues/151
repos:
  - buchbend/lore
---

# PRD 0002: Session notes that carry the essence, one per session

> Source of truth for this epic. Tracker: [epic issue](https://github.com/buchbend/lore/issues/151).
> The epic links here; this file is not embedded in the issue body.

## Problem

The 0.56.0 essence-first rewrite improved the *voice* of session notes but a
pilot against real transcripts exposed two failure classes that still make the
notes unhelpful when a colleague returns to them months later.

**One session produces several unlinked, partly-duplicated notes.** A single
work session (transcript `a737ff12`, 2026-07-04) filed as two separate note
files — `04-0605-publish-gate.md` (turns 1095–1383) and
`04-0608-...-record-the-work-not-the.md` (turns 1384–1430) — split at a
`/compact`. The second note re-narrated facts the first already carried,
because it started from an empty note-so-far. The vault accumulates sibling
notes that each tell part of the story and repeat the rest.

**Notes report material the user never worked on.** `04-0605-publish-gate.md`
presents SOPS / `age` / `.env` / `tmpfs` security *findings* as the session's
work. The session was about Lore's own note-compose quality; the user merely
**pasted an old SOPS note as a formatting exemplar**. The composer read the
pasted, self-anchored example blocks and attributed their claims to the
session. A related tell: every block in that note cites the same `@1095`
anchor — the single turn holding the pasted blob — so the anchors are
worthless as navigation.

Both classes defeat the product promise: a note should be a faithful,
skimmable, navigable record of *what this session figured out*.

## Solution

From the user's perspective, after this epic:

- **One session is one note.** A session that is closed early (a false
  liveness reap) or resumes after a genuine close (editor restart, a
  `/compact`, an idle-then-return) continues its **existing** note instead of
  spawning a duplicate sibling. Notes never repeat content across files.
- **Notes describe only what the session worked on.** Material the user pastes
  or quotes as an exemplar, reference, or comparison is never reported as the
  session's own findings.
- **Anchors point somewhere useful.** When several findings all cite one turn,
  the composer is nudged to anchor them to where each actually arose, or to
  confirm they are genuinely one finding.
- **Note filenames name their topic**, derived from the first composed lead
  rather than an incidental heuristic.

## Implementation decisions

Work is confined to `buchbend/lore`, across two subsystems.

### Buffer lifecycle — one session, one note

- **Liveness (`lore_curator/reaper.py::is_owner_alive`).** In the
  buffer-and-flush architecture the owner `pid` is re-stamped every heartbeat
  to the *current hook subprocess*, which exits within milliseconds — so
  "pid gone" is the normal steady state, not death. Today a same-host,
  pid-absent owner returns `False` (dead), letting the reaper and startup
  sweep `synth_and_close` a **live** session. It must return `None`
  (uncertain) instead, deferring to the existing staleness threshold. A
  genuinely dead session still closes — via staleness, just not instantly.
  A cross-host owner stays `False` (unchanged).
- **Reopen + continuation (`lore_core/note_document.py`,
  `lore_curator/buffer_append.py`, `session_note.py`, `buffer_store.py`).** A
  new `reopen_note` primitive flips a closed note's `note_status` back to
  `open`. In `append_chunk`'s `existing is None` branch — the point where a
  resumed session currently mints a fresh sidecar with an empty `stub_path`
  and thus a new file — the code first looks in `_done/` for an archived
  sidecar of the same stem (`<transcript_id>__<local_date>`); if found it
  restores that buffer, reopens its note, and continues appending chapters to
  the **same file**, seeding note-so-far from the existing body so the compose
  does not duplicate.

  **ADR-worthy decision:** this deliberately relaxes the invariant "a closed
  session note is immutable" for **session notes**. The chosen model is *one
  file per session* (reopen), not a linked chain of sibling notes. Immutability
  still holds for derived/curated artifacts; only a session's own note may be
  reopened by its own continuing session. This is recorded as an ADR and the
  vault-edit-policy is updated to carve out the exception.

### Compose quality

- **Quoted-vs-worked distinction (`lore_curator/chapter_compose.py::_build_prompt`).**
  An explicit clause tells the composer that content the user pastes or quotes
  as an exemplar / reference / comparison (signalled by framing like "this is a
  form I'd like", fenced blocks, or blocks that carry their own `@N`-anchored
  bold leads) is **not** this session's work; its claims are never attributed
  to the session unless working on that material was itself the topic. Written
  explicitly because the compose model is a ~120B open model, weaker at
  implicit pragmatics.
- **Same-anchor soft lint (`lore_curator/chapter_compose.py`).** The existing
  anchor-lint / retry-feedback plumbing gains a *soft* check: when a chapter
  has more than two blocks all citing one anchor, one corrective retry is
  issued ("these all cite the same turn — confirm they are distinct findings
  from session progression, not restatements of one quoted passage"). It never
  hard-rejects and never fabricates anchor diversity; after the retry the
  chapter publishes regardless.
- **Topic slug (`lore_curator/session_note.py::_derive_slug`).** Derive the
  note filename slug from the first composed lead; fall back to the current
  heuristic for stubs that have no chapter yet.

## Testing decisions

Tests assert external behavior, not internals, and use the stub-LLM replay
harness already established in `test_chapter_compose.py` /
`test_chapter_flush.py` (no LLM-as-judge; every model call is faked).

- **Liveness:** a regression test reproducing the false reap — a heartbeat pid
  exits, the reaper runs, and the session must survive; plus the boundary that
  a truly stale pid-gone buffer still closes. Prior art:
  `test_startup_sweep.py`, `test_reaper.py`.
- **Continuation:** seed an archived buffer (`_done/`) with a closed note, feed
  a new chunk on the same stem, assert a **single** note file results with the
  new chapter appended and no duplicated body; a `reopen_note` unit test for
  the open/append/close cycle. Reproduce the concrete `04-0605`/`04-0608`
  split as the acceptance fixture.
- **Quoted material:** replay transcript `a737ff12` turns 1095–1383 and assert
  the SOPS/tmpfs claims are absent from the composed output, while a session
  that genuinely works on pasted content still reports it.
- **Same-anchor lint:** deterministic unit tests — >2 blocks one anchor →
  exactly one corrective retry then publish; distinct anchors untouched.
- **Slug:** a composed note's slug reflects its first lead; stubs keep a safe
  fallback; no same-minute collision.

## Out of scope

- **Re-composing the notes already filed under the broken rules** (the existing
  duplicate/misattributed notes). Tracked separately under the spirit of #49
  (re-flush pre-existing notes); this epic fixes forward.
- **A redesigned owner-identity scheme** (e.g. a stable session-level owner id
  replacing the ephemeral pid). Slice 1 routes pid-gone through the existing
  staleness path, which is sufficient; a deeper owner model is a possible
  follow-up.
- **Mid-session cap-trip flush (#145)** as an independent defect. It is
  plausibly the same false-reap root; the epic links it and it is closed if
  slice 1 resolves it, but no separate slice is committed to it.
- **The curator lock 1-hour lockout (#144).**
