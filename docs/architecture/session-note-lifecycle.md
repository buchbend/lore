# Session-note lifecycle — one session, one note

**Audience:** contributors who open a note whose frontmatter says
`note_status: closed`, watch it gain another chapter an hour later, and
wonder how a "closed" note is still being written to — or who need to
know why a resumed session no longer leaves two half-duplicated notes
behind.

The session note is the deterministic core's one-record-per-session
artifact: a fixed disclaimer followed by chronological chapters, one per
flush. `CONTEXT.md` is the vocabulary (buffer, flush, chapter, block,
anchor, marker); this document explains the **lifecycle** that vocabulary
moves through — `create → append → close → reopen` — and the guarantee
that lifecycle exists to keep: **one session produces exactly one note.**

---

## The promise, and how it used to break

A session note should be a faithful, skimmable, navigable record of what
*one* session figured out. Two failure classes broke that promise even
after the note *voice* was fixed:

- **One session, several notes.** A session closed early (a false
  liveness reap) or resumed after a `/compact`, an editor restart, or an
  idle-then-return would mint a *second* note file that re-narrated the
  first from an empty note-so-far. The vault accumulated sibling notes
  that each told part of the story and repeated the rest.
- **Notes reporting work the session never did.** Material the user
  pasted only as a formatting exemplar or a reference was read by the
  composer and reported as the session's own findings — with every block
  collapsing onto the single turn that held the pasted blob, so the
  anchors were useless for navigation.

The lifecycle below closes both. The full problem statement and the
per-decision rationale live in
[PRD 0002](../prd/0002-useful-session-notes.md); the immutability
trade-off is recorded in
[ADR 0001](../adr/0001-session-note-reopen-relaxes-close-immutability.md).

---

## Keeping one session to one note

Two mechanisms cooperate: a session is not closed while it is merely
quiet, and if it *is* closed and then resumes, it reattaches to its own
note instead of starting a new one.

### 1. Liveness — "pid gone, same host" is uncertain, not dead

The buffer records an owner `pid`, re-stamped every heartbeat to the
current hook subprocess — which exits within milliseconds. So "the owner
pid is gone" is the **normal steady state of a live session**, not a
death signal. `lore_curator/reaper.py::is_owner_alive` returns:

| Owner state | Verdict | Consequence |
|---|---|---|
| Same host, pid signalable, `/proc` start-ts matches | `True` (alive) | keep waiting |
| Owner `host` differs from this host | `False` (dead) | reap immediately |
| Same host, pid gone / start-ts mismatch / no `/proc` access | `None` (uncertain) | fall back on the staleness threshold |

The critical case is the last row. A same-host pid-gone owner is
**uncertain**, so the reaper and the SessionStart sweep defer to
`liveness_stale_threshold_s` (default 30 min, doubled on macOS) instead
of closing instantly. A genuinely dead session still closes — just via
staleness, a little later — so no buffer leaks. Only an *unambiguously*
dead owner (a different host) is reaped on sight. This alone stops most
of the duplicate-note churn, because the live session that was being
reaped mid-flight now survives.

### 2. Reopen + continue — a resume reattaches to its own note

Staleness still closes a session that goes genuinely idle and later
comes back. When it comes back, the next heartbeat finds no live buffer
(it was archived to `_done/`) and would, historically, mint a fresh
sidecar with an empty note pointer and thus a **second** file.

Instead, at the `existing is None` branch of
`buffer_append.py::append_chunk`, the code first calls
`buffer.reopen_from_done()`: it looks in `_done/` for an archived buffer
of the **same stem** (`<transcript_id>__<local_date>`) and, if found,
*moves* it back to the live directory and continues appending chapters
to the same note. The archived note is reopened with
`lore_core/note_document.py::reopen_note`, and note-so-far is seeded from
the existing body so the next compose does not repeat earlier content.
The drain emits `buffer-reopened` rather than a second `note-filed`.

If the prior note was discarded (a trivial/empty session whose file was
deleted), the restored pointer dangles; the restore detects the missing
file, clears the pointer, and lets a fresh note be created — it never
crashes trying to reopen a deleted file.

**This deliberately relaxes one invariant.** Before this epic, `close`
was terminal: a note with `note_status: closed` was immutable and
`append_chapter` raised `NoteClosedError`. `reopen_note` flips that
status back to `open` so a resumed session can append. The carve-out is
narrow and its trade-offs are the subject of
[ADR 0001](../adr/0001-session-note-reopen-relaxes-close-immutability.md):

- Only a session's **own** continuing heartbeat, keyed on the identical
  stem, ever reopens its own note. No cross-session or curator writer can.
- Immutability still holds for every derived/curated artifact (concepts,
  decisions, threads).
- Reopen only ever *appends* — earlier chapters are never edited, so the
  append-only-within-a-session contract is intact. What changes is that
  `note_status: closed` is no longer a guarantee of *permanence* for a
  session note; the same file may gain chapters if its session resumes.

---

## Recording only what the session worked on

One session, one note is worthless if that one note reports the wrong
work. Two compose-side checks keep the record faithful. Both live in
`lore_curator/chapter_compose.py` and both are written explicitly
because the compose model is a ~120B open model, weak at implicit
pragmatics.

### Quoted and reference material is not the session's work

`_build_prompt` carries an explicit clause: content the user pastes or
quotes as an **exemplar, reference, or comparison** — signalled by
framing ("this is a form I'd like", "for example", "an older version
of"), by a fenced code block, or by a block that carries its **own**
`@N`-anchored bold leads (the shape of an earlier note) — is *about* that
pasted material and does **not** describe what this session did. Its
claims, tools, paths, and config are never reported as the session's
findings. The one exception: when working on that material *was* the
topic (the user asked to review, fix, or reason about it), that work is
the session's work and is recorded. The chapter tool schema's `body`
description reinforces the same rule.

### Same-anchor soft lint — nudge honest anchors

`chapter_same_anchor_lint` flags a chapter with **more than two** blocks
all citing one turn — the tell of several "findings" derived from a
single pasted passage. Two blocks sharing a turn is common and
unremarkable; more than two is usually a collapse that makes the anchors
useless for navigation. On a hit, the compose loop issues **exactly one**
corrective retry (`_same_anchor_feedback`: "confirm these are distinct
findings … re-anchor each block to the turn where its own topic actually
started"). It is a **soft** signal: it never hard-rejects and never
fabricates anchor diversity — after the single nudge the chapter
publishes regardless of whether the anchors changed. This sits beside the
existing hard `chapter_anchor_lint` (which rejects out-of-range anchors),
reusing the same retry plumbing.

---

## Naming the file after its topic

The note file is created — and first named — at the first heartbeat,
before any chapter exists, so its initial slug is an incidental guess (a
commit subject, a touched file's basename, or a bare timestamp). Once the
first chapter composes, its opening lead names the session's actual
topic, and `chapter_flush.py::_rename_to_topic_slug` renames the file to
match (only on chapter 1). A chapter with no usable lead, or a lead that
slugs to nothing or to the bare fallback `session`, leaves the filename
untouched rather than risk an empty slug; same-minute collisions still
get a numeric suffix. Stub notes that never compose a chapter keep their
fallback name.

---

## How failures surface

A flush can fail in two different places, and each surfaces differently.

**Inside compose — a marker chapter in the note itself.** This is the
give-up semantics from "Writing a note" above: mid-session failure is
silent while a retry chance remains, but a buffer that grows to 2x the
cap with a prior failed attempt gets a deterministic **failed marker**
chapter, and a publish-gate withhold gets a **withheld marker** chapter
(the full composed text goes to the private quarantine sidecar instead).
Either way the note stays open and readable — the marker chapter *is*
the failure record, right where a reader would look for the missing
content.

**Outside compose — a dead-lettered flush on the spine.** Everything
between "a hook decides a flush is needed" and "compose starts" —
spawn failures, buffer-sidecar read errors, chapter-append I/O errors —
used to fail silently. The flush lifecycle state machine
(`lore_core/flush_store.py`, issue #189) makes this queryable instead: a
flush that exhausts its bounded retries (3 attempts, exponential
backoff) becomes a `dead-lettered` record with a structured reason
instead of vanishing. `lore trace dead` lists them; `lore status`'s
alerts section surfaces a nonzero dead-letter count and names that same
command. Every transition — including the ones that don't dead-letter —
also lands on the event spine (`source="curator"`,
`event="flush-<state>"`), so `lore trace <trace-id-or-session-id>`
replays the whole path a specific flush took, marker chapter or not.

A hard failure of the spine *writer* itself (an `OSError` mid-`emit`) is
the one thing that can't be a spine event — it touches
`spine-failed.marker` instead, which `lore doctor` and `lore status`
check for directly.

---

## See also

- `CONTEXT.md` — the note vocabulary (buffer, flush, chapter, block,
  anchor, marker) and the buffer/flush/compose write path.
- [`observability.md`](observability.md) — the event spine's full
  envelope shape, producer list, trace_id lifecycle, and retention
  policy behind the dead-letter/marker distinction above.
- [ADR 0001](../adr/0001-session-note-reopen-relaxes-close-immutability.md)
  — the close-immutability carve-out, its alternatives, and its
  concurrency and trade-off analysis.
- [PRD 0002](../prd/0002-useful-session-notes.md) — the problem
  statement, the pilot transcript that exposed both failure classes, and
  the per-decision rationale.
- Source: `lore_curator/reaper.py` (liveness),
  `lore_curator/buffer_append.py` + `lore_core/note_document.py`
  (reopen + continue), `lore_curator/chapter_compose.py` (compose
  fidelity), `lore_curator/chapter_flush.py` (topic slug).
