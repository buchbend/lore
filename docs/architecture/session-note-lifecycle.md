# Session-note lifecycle — one session, one note

**Audience:** contributors who watch a long session run for hours without a
note appearing anywhere, or who open a note whose frontmatter says
`note_status: closed`, see it change an hour later, and wonder how a "closed"
note is still being written to.

The session note is the deterministic core's one-record-per-session artifact:
a fixed disclaimer, a rendered reading of the session, and below it the
append-only ledger the reading was computed from. `CONTEXT.md` is the
vocabulary (buffer, flush, chapter, fact, anchor, marker); this document
explains the **lifecycle** that vocabulary moves through — `create → close →
segment → extract → render → seal` — and the guarantee it exists to keep:
**one session produces exactly one note.**

---

## One flush, and it is the ending

The note file is created at the session's first heartbeat — disclaimer,
frontmatter, no content. After that, **nothing is written to it while the
session runs.** Turns accumulate in the buffer and that is all.

Which of a session's turns mattered is only knowable *backward*, from its
ending: at the moment a slice happens, "PR #524 opened" genuinely is its
substance, and only the ending reveals that twenty such lines collapse into
"all five features merged". So the whole session is read at once, at the close,
and the note appears once, complete. The reasoning behind that is
[why a session note is written only at the end](../explanation/why-notes-are-written-at-session-end.md);
this page is the mechanics.

**`capture_routing.CLOSE_TRIGGERS` is the single authority** for which trigger
flushes, and it holds exactly one entry: `session-end`. Everything else
bookkeeps.

| Event | What happens |
|---|---|
| Buffer trips its cap (`synthesis_buffer_cap_turns` / `_chars`) | A `cap-tripped` event is recorded on the buffer. It keeps accumulating. No chapter, no model call. |
| Pre-compact | Forwarded to `request_flush_for_my_buffers`, which drops it — not in `CLOSE_TRIGGERS`. The buffer keeps accumulating. |
| Session end | The buffer's `flush_requested` is stamped and the close path runs. |
| Reaper / startup sweep | The same close path, for a session whose owner died without one. |

Session end's **unconditional drain** is preserved: a buffer that tripped its
cap and kept growing still drains the whole session at close, cap or no cap.
The cap is now a bookkeeping marker — it says the buffer got large, and
nothing else acts on it.

The close path is `lore_curator/chapter_flush.py::synth_and_close`:

```
segment_session   (cheap model — returns turn INDICES only)
  → extract_session (one call per chunk — typed facts; plus one headline)
  → publish gate
  → append_facts | withheld marker + quarantine | failed marker
  → render_note   (pure code — verify refs, stamp phrasing, lay out the body)
  → close_note
```

Every LLM call a note costs is in the first two steps. The segmenter
(`lore_curator/chunker.py`) reads a collapsed view of the session and returns a
list of integers — its entire output surface, so its errors degrade to
suboptimal windows and never to false facts. The extractor
(`lore_curator/fact_extract.py`) reads the raw turns of one chunk at a time and
emits typed facts: a `kind` (`progress` | `done` | `decision` | `finding` |
`open`), a `thread` key, structured `refs`, a short `text`, a `why` (mandatory
for a decision), and one `@turn` anchor.

Nothing downstream is generative. **The ledger is append-only; the body above it
is a derived render** — thrown away and recomputed in full from the ledger at
every close ([ADR 0003](../adr/0003-note-body-is-a-derived-render-of-the-ledger.md)).
Section order, anchor sort, thread suppression and every word that carries
epistemic weight are code
([ADR 0004](../adr/0004-authority-phrasing-is-code-stamped.md)). The same ledger
renders to the same bytes, always.

---

## Keeping one session to one note

Two mechanisms cooperate: a session is not closed while it is merely quiet, and
if it *is* closed and then resumes, it reattaches to its own note instead of
starting a new one.

### 1. Liveness — "pid gone, same host" is uncertain, not dead

The buffer records an owner `pid`, re-stamped every heartbeat to the current
hook subprocess — which exits within milliseconds. So "the owner pid is gone" is
the **normal steady state of a live session**, not a death signal.
`lore_curator/reaper.py::is_owner_alive` returns:

| Owner state | Verdict | Consequence |
|---|---|---|
| Same host, pid signalable, `/proc` start-ts matches | `True` (alive) | keep waiting |
| Owner `host` differs from this host | `False` (dead) | reap immediately |
| Same host, pid gone / start-ts mismatch / no `/proc` access | `None` (uncertain) | fall back on the staleness threshold |

The critical case is the last row. A same-host pid-gone owner is **uncertain**,
so the reaper and the SessionStart sweep defer to `liveness_stale_threshold_s`
(default 30 min, doubled on macOS) instead of closing instantly. A genuinely
dead session still closes — just via staleness, a little later — so no buffer
leaks. Only an *unambiguously* dead owner (a different host) is reaped on sight.

### 2. Reopen + re-render — a resume reattaches to its own note

Staleness still closes a session that goes genuinely idle and later comes back.
When it comes back, the next heartbeat finds no live buffer (it was archived to
`_done/`) and would, historically, mint a fresh sidecar with an empty note
pointer and thus a **second** file.

Instead, at the `existing is None` branch of `buffer_append.py::append_chunk`,
the code first calls `buffer.reopen_from_done()`: it looks in `_done/` for an
archived buffer of the **same stem** (`<transcript_id>__<local_date>`) and, if
found, *moves* it back to the live directory. The archived note is reopened with
`lore_core/note_document.py::reopen_note`, and the second close appends the new
facts to the ledger and **re-renders the whole body over it** — a reading of the
whole session, not a second reading stacked under the first. The drain emits
`buffer-reopened` rather than a second `note-filed`.

If the prior note was discarded (a trivial/empty session whose file was
deleted), the restored pointer dangles; the restore detects the missing file,
clears the pointer, and lets a fresh note be created — it never crashes trying
to reopen a deleted file.

**This deliberately relaxes one invariant.** Before, `close` was terminal: a
note with `note_status: closed` was immutable and any append raised
`NoteClosedError`. `reopen_note` flips that status back to `open` so a resumed
session can add to it. The carve-out is narrow and its trade-offs are the
subject of
[ADR 0001](../adr/0001-session-note-reopen-relaxes-close-immutability.md):

- Only a session's **own** continuing heartbeat, keyed on the identical stem,
  ever reopens its own note. No cross-session or curator writer can.
- Immutability still holds for every derived/curated artifact (concepts,
  decisions, threads).
- The **ledger** is only ever appended to — a fact is never edited or deleted
  once written, and keeps its quote and its anchor. The **body** is the part
  that is rewritten, which ADR 0003 states outright.

---

## Recording only what the session worked on

One session, one note is worthless if that one note reports the wrong work. The
extraction prompt (`lore_curator/fact_extract.py::_build_prompt`) carries four
rules explicitly, because the extraction model is a small open model, weak at
implicit pragmatics:

- **The month test.** A fact earns its place only if it would change what a
  colleague does or believes a month from now. Few facts, or none, is the normal
  answer for a chunk.
- **The terminal-state rule.** `done` means a terminal state and nothing else: a
  commit landed, a PR merged, a suite verified green. Work en route — an edit
  made, a draft opened, a review requested — is `progress`, however much it felt
  like a milestone at the time.
- **The supervision clause.** When the session supervises other agents, the
  subject is the *deliverable*, not the choreography. Dispatching a teammate or
  posting a status comment is not a fact; what the teammate delivered, and
  whether it landed, is.
- **Quoted and reference material is not the session's work.** Content the user
  pastes as an exemplar, a reference, or a comparison — a formatting sample, an
  older note of their own — describes *that material*, not this session. Its
  claims, tools and paths are never reported as the session's facts. The
  exception: when working on that material *was* the task.

Three deterministic lints police the result, and each earns **exactly one**
corrective retry before the chunk degrades: an anchor outside the chunk, a
`kind` outside the enum, a `decision` with no `why`. The anchor lint is also
what makes the Stille-Post rule structural rather than hopeful — each extraction
call sees the compact fact table of the calls before it, for thread continuity
and dedup only, and any fact lifted from that table is anchored outside the
chunk and rejected. No model in this pipeline reads model prose as source
material.

Quotes are **code-attached** from the anchor turn. The extraction tool schema
has no quote field, so the model cannot author the verbatim evidence for its own
claim.

---

## Naming the file after its topic

The note file is created — and first named — at the first heartbeat, before any
content exists, so its initial slug is an incidental guess (a commit subject, a
touched file's basename, or a bare timestamp). At close, one bounded call writes
the session's **headline** from the fact table (the only cross-chunk synthesis
in the pipeline; a lint rejects a headline naming a ref or thread the table does
not contain, and a headline that cannot pass it is dropped rather than
published). The headline names both the frontmatter `title`
(`stub_note._scope_title` → `<scope>: <headline>`) and the filename
(`chapter_flush._rename_to_topic_slug`) — so title and filename always name the
same topic. An empty headline, or one that slugs to nothing or to the bare
fallback `session`, leaves the filename untouched rather than risk an empty
slug; same-minute collisions get a numeric suffix.

---

## How failures surface

A close can fail in two different places, and each surfaces differently.

**Inside the pipeline — a marker chapter, and a coverage gap in the reading.**
A chunk the model cannot extract becomes a deterministic **failed marker** for
its span; a chunk the publish gate withholds becomes a **withheld marker** (the
full text goes to the private quarantine sidecar instead). Both are ledger
chapters, so both are chapters the rendered body cannot speak for — and every
non-`facts` chapter renders as a one-line **coverage gap** under Open: *"Coverage
gap: turns 40–71 are not covered by this note."* One bad chunk never costs the
rest of the session, and a partial note can never present itself as complete.
The same is true of a legacy prose chapter, which still parses but contributes
no facts.

There is no silent mid-session defer and no give-up bound any more; both were
properties of the per-flush composer. A close either extracts, withholds, or
fails — and the note ends closed either way.

**Outside the pipeline — a dead-lettered flush on the spine.** Everything
between "a hook decides a flush is needed" and "extraction starts" — spawn
failures, buffer-sidecar read errors, chapter-append I/O errors — used to fail
silently. The flush lifecycle state machine (`lore_core/flush_store.py`) makes
this queryable instead: a flush that exhausts its bounded retries (3 attempts,
exponential backoff) becomes a `dead-lettered` record with a structured reason
instead of vanishing. `lore trace dead` lists them; `lore status`'s alerts
section surfaces a nonzero dead-letter count and names that same command. Every
transition also lands on the event spine (`source="curator"`,
`event="flush-<state>"`), so `lore trace <trace-id-or-session-id>` replays the
whole path a specific flush took, marker chapter or not.

A hard failure of the spine *writer* itself (an `OSError` mid-`emit`) is the one
thing that can't be a spine event — it touches `spine-failed.marker` instead,
which `lore doctor` and `lore status` check for directly.

---

## See also

- [`why-notes-are-written-at-session-end.md`](../explanation/why-notes-are-written-at-session-end.md)
  — why the pipeline runs backward from the ending, and why code owns the
  phrasing.
- `CONTEXT.md` — the note vocabulary (buffer, flush, chapter, fact, anchor,
  marker) and the buffer/flush/render write path.
- `CONTEXT-FORMAT.md` — the exact title and body shape a rendered note takes.
- [`observability.md`](observability.md) — the event spine's full envelope
  shape, producer list, trace_id lifecycle, and retention policy behind the
  dead-letter/marker distinction above.
- [ADR 0001](../adr/0001-session-note-reopen-relaxes-close-immutability.md) —
  the close-immutability carve-out.
- [ADR 0003](../adr/0003-note-body-is-a-derived-render-of-the-ledger.md) — the
  body as derived state over an append-only ledger.
- [ADR 0004](../adr/0004-authority-phrasing-is-code-stamped.md) — ref
  verification and code-stamped phrasing.
- [PRD 0008](../prd/0008-typed-fact-session-notes.md) — the problem statement
  and per-decision rationale for typed-fact notes.
- Source: `lore_curator/reaper.py` (liveness), `lore_curator/buffer_append.py`
  (bookkeeping + reopen), `lore_curator/chunker.py` (segmentation),
  `lore_curator/fact_extract.py` (extraction), `lore_curator/chapter_flush.py`
  (the close path), `lore_core/note_document.py` +
  `lore_core/ref_verify.py` (render, verify, stamp).
