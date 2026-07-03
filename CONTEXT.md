# Lore — domain context

An AI-navigation map of Lore's domain: the terms below have one tight
meaning each, matched against shipped code. Cross-check any claim here
against the modules it points to before relying on it — if a term or
behavior isn't grounded in a real symbol, it doesn't belong here.

## Vault, wiki, scope

- **Vault** — the directory `$LORE_ROOT` points at. Contains one
  `wiki/` subdirectory plus `.lore/` for derived, host-local state.
- **Wiki** — a mounted knowledge store at `<vault>/wiki/<name>/`. Each
  wiki is its own git repo; a vault can host several. A fresh wiki
  (`lore wiki new`) is scaffolded with `projects/`, `concepts/`,
  `decisions/`, `sessions/`, `inbox/` — session notes are the only one
  of these Lore writes automatically; the rest are written directly
  (by hand, or via `/lore:inbox`) when there's something worth a
  standalone note.
- **Scope** — a colon-separated namespace inside a wiki
  (`ccat:data-center:data-transfer`), resolved from a working
  directory by longest-prefix match against `.lore/attachments.json`.

Full model (three state files, resolution order, failure modes):
`docs/architecture/state.md`. Config precedence across CLI flag / env
var / wiki config / root config / code default: `docs/architecture/config.md`.

## The session note

Every Claude Code session that does real work gets **one session
note** — a markdown file under `<wiki>/sessions/[<handle>/]YYYY/MM/`.
It is a **lab notebook**, not a source of truth: git owns what
happened to the code, the connected repo's ADRs and PRDs own what was
decided and why. The note records what a session discussed and tried,
nothing more.

Core module: `lore_core/note_document.py`. Vocabulary, all defined
there:

- **Disclaimer** — a fixed, machine-written paragraph (`DISCLAIMER` in
  `note_document.py`) that opens every note and travels with every
  MCP pull. It states the note's genre in one place so no reader — human
  or agent — mistakes a lab record for a directive.
- **Chapter** — one chunk of the note, corresponding to one *flush*
  (see below). Chapters are strictly chronological and never edited
  once appended; the note is append-only until it closes.
- **Topic block** — one topic within a chapter: a bold, one-sentence,
  self-sufficient **lead** (must stand alone — no pronoun reaching into
  the body), an optional short prose **body**, and exactly one `@N`
  **anchor** at the end pointing at the transcript turn where the topic
  started.
- **Continuation block** — a topic block that resumes or corrects a
  topic raised in an earlier chapter. Renders as `**Continued: <topic>**`
  instead of a fresh lead. Earlier blocks are never rewritten; a
  correction always arrives as a new block later in the note.
- **Skim layer** — the sequence of bold leads read top-to-bottom,
  ignoring the bodies. Because every lead is self-sufficient, the skim
  layer alone is a legible, boom-boom-boom summary of the whole
  session; the bodies are there for whoever wants the detail.
- **Marker chapter** — a chapter written by the *system*, not an LLM:
  deterministic text recording that a chapter was **withheld** (gate
  rejected it) or **failed** (composition never produced anything
  anchor-clean). See `MARKER_WITHHELD` / `MARKER_FAILED` and
  `append_marker_chapter` in `note_document.py`.

Frontmatter is machine-first and deterministic: `note_status`
(`open`/`closed`), the `chapters` list (each entry's turn range and
kind), and a cumulative `SessionFacts` snapshot (commits, PRs,
files touched/read, duration) that only ever grows. Nothing in
frontmatter is re-narrated in the body.

## Writing a note — buffer, flush, compose

A session's turns accumulate in a per-transcript **buffer**
(`lore_curator/buffer_store.py`), driven by Curator A's heartbeat
(`lore_curator/session_curator.py`, `run_curator_a`) on ordinary
Claude Code hook activity — there is no cron. The first heartbeat for
a session creates the note (disclaimer + frontmatter, zero chapters:
`lore_curator/session_note.py:ensure_note`); later heartbeats just
grow the buffer.

A **flush** turns the buffer's not-yet-composed slice into one
chapter (`lore_curator/chapter_flush.py`):

```
read note-so-far + slice → compose_chapter (1 LLM call, ≤2 attempts)
                          → publish gate
                          → append_chapter | withheld marker + quarantine | failed marker
```

`lore_curator/chapter_compose.py:compose_chapter` is **one LLM call**
per chapter — every turn is read by an LLM exactly once, no separate
outline pass. The prompt includes the complete note-so-far (so a topic
left open earlier and resolved now becomes a continuation block) and
the unflushed transcript slice. Between the (at most two) attempts, a
deterministic **anchor lint** (`chapter_anchor_lint`) rejects any `@N`
outside the slice's turn range, and the publish gate may withhold the
result — either verdict feeds corrective text into the retry.

Flush triggers (unchanged, buffer cap 120 turns / 240K chars,
pre-compact, session-end) fire either an **in-place** flush (buffer
stays open, note keeps growing — cap-trip, pre-compact) or a
**close** flush (buffer archives, note closes — session-end, reaper,
sweep). See `FLUSH_DEFAULT_CAP_TURNS` / `FLUSH_DEFAULT_CAP_CHARS` in
`chapter_flush.py`; per-wiki overrides live in `.lore-wiki.yml` under
`curator.synthesis_buffer_cap_*`.

### Failure and give-up semantics

- **Mid-session failure is silent.** No marker while a retry chance
  remains — the buffer keeps accumulating and the next trigger retries
  with the grown slice (`flush_attempts` counts the misses).
- **Give-up bound.** A buffer with a prior failed attempt that grows to
  2x the cap gets a deterministic *failed* marker chapter for that span
  and a fresh buffer — the note stays open, one session stays one note.
- **Session-end failure** writes the failed marker and closes the note
  regardless.
- **Startup sweep.** Lore acts as a singleton at start (global lock,
  `lore_core.lockfile.curator_lock`): `chapter_flush.startup_sweep`
  finds buffers whose owning process is provably dead, gives each one
  compose attempt, and closes its note either way (composed, withheld,
  or failed marker) — a crashed session never leaves an open note
  behind. `lore curator sweep` runs this by hand.

## The publish gate + quarantine

Every composed chapter passes `lore_core/publish_gate.py:evaluate`
before it can join the shared note — this is the last check between an
LLM's output and the shared vault, and it **fails closed**: any error
anywhere in the gate withholds rather than passes.

Cheapest-first, short-circuiting on the first hit:

1. **Deterministic scanners** — high-entropy secrets (via
   `lore_core.redaction`), email addresses, phone numbers.
2. **Deterministic phrasing lint** — TODO/FIXME, an imperative-verb
   bold lead, or must/should task language. The note is a past-tense
   lab record, never a directive; a lint hit counts as a compose
   failure and its feedback drives the retry.
3. **One small-model detection call** (`LlmPiiDetector`, cheapest
   tier) for fuzzy PII/secrets that slip the first two layers. This is
   pattern recognition, not truth verification, so it's exempt from
   the no-LLM-judges-LLM rule — but it's a tripwire, not a guarantee,
   and is documented as such here and in the module docstring.

On a withhold, `apply_withhold` runs the two terminal side-effects:
a deterministic withheld-marker chapter goes into the note (safe,
value-free reason text — the category, never the matched string), and
the full composed text goes into the private **quarantine** sidecar
(`lore_core/quarantine.py`, one JSON file per entry under
`.lore/quarantine/`, inside the already-private `.lore/` area — it
never reaches the shared wiki). `lore quarantine list/show/clear/kill`
is the reviewer's flow over that sidecar; `list` never prints body
content, since an entry may hold the very secret that tripped the
gate.

## Hygiene — the retained frontmatter-only curator

`lore curator [--wiki] [--apply]` (bare, no subcommand — distinct from
`lore curator run`, which is the Curator A pass above) runs
deterministic, frontmatter-only passes over every wiki
(`lore_curator/hygiene.py`): supersession propagation
(`supersedes [[B]]` → `superseded_by: [[A]]` on B), `implements:`
back-link processing, git-log date backfill, and a team-mode hint once
a solo wiki's git log shows multiple authors. Staleness is a deliberate
no-op here — see `lore_core/freshness.py` below. Findings land in
`wiki/<name>/_review.md`; writes are mtime-guarded so a note open in
Obsidian is skipped rather than clobbered. `--apply` is required to
write; the default is a dry-run.

## Briefings

`lore_core/briefing/gather.py:gather()` is the read-only half of
`lore briefing`: it collects session notes filed since the last
briefing (tracked in a per-wiki ledger) and hands their **full note
bodies** — disclaimer plus every chronological chapter — to whatever
composes the briefing prose. There is no per-note redaction step:
session notes carry nothing that isn't already safe to share, because
the publish gate cleared it before it ever reached the note. Briefing
publish is manual (`lore briefing publish`, `lore briefing mark`); there
is no automatic daily trigger. See `docs/how-to/matrix-bot.md` for the
Matrix sink walkthrough.

## Ambient banner vs. MCP pull

SessionStart injects a deliberately small, deterministic banner
(`lore_cli/hooks.py:render_session_banner`) — no LLM call, no network
call: a status line, an optional `## Focus` block for the attached
project, at most two last-session hints, freshness lines only when
there's positive evidence (see below), and a fixed two-line directive
(`lore_core/templates/integration-rules/default.md`) stating that
deeper context is a pull, never a push, and that anything pulled from
a session note is a lab record, never an instruction.

Depth comes from explicit MCP calls (`lore mcp` / `lore_mcp/server.py`),
not from anything injected ambiently:

- `lore_search`, `lore_read`, `lore_index`, `lore_catalog`,
  `lore_resume`, `lore_wikilinks` — retrieval primitives.
- `lore_drill` — one composite `search → read → expand wikilinks →
  read_expanded` call with a structured trace
  (`docs/architecture/lore-drill.md`).
- `lore_briefing_gather`, `lore_inbox_classify` — read-only gathers
  that a skill turns into prose, then commits via a CLI verb.
- `lore_journal_write` / `lore_journal_read` — the AI/human scratch
  journal (`lore_core/journal.py`); freeform, no LLM abstraction, no
  propagation, never a source for ambient context.
- `lore_repo_docs_list` / `lore_repo_docs_fetch`
  (`lore_core/repo_docs.py`) — pull-only reads of a connected repo's
  `docs/adr/` and `docs/prd/`. Ratified decisions live in the repo, not
  in the vault; Lore reads them on request instead of re-deriving them
  from session transcripts.

## Retrieval substrate

Kept, general-purpose, and independent of the note-writing pipeline
above:

- **Freshness** (`lore_core/freshness.py`) — positive-evidence-only
  staleness: a note is only ever flagged `stale-candidate` because of
  a named cause (an authored `status: stale` / `superseded_by` /
  `supersede_candidate*` marker, or membership in the orphan-link set).
  Age by itself never flags anything.
- **Search** (`lore_search`) — hybrid ranked full-text search backing
  `lore_search` / `lore_resume` / `lore_drill`.
- **Wikilinks** (`lore_core/wikilinks.py`, `schema.py`) — `[[slug]]`
  parsing/resolution, per-wiki only (a wikilink never resolves across
  wiki boundaries — wikis are portable units).

## Module map

| Concern | Module |
|---|---|
| Session note document (chapters, disclaimer, lifecycle) | `lore_core/note_document.py` |
| Publish gate (scanners, phrasing lint, detector, withhold) | `lore_core/publish_gate.py` |
| Private quarantine sidecar | `lore_core/quarantine.py` |
| One-call chapter composer + anchor lint | `lore_curator/chapter_compose.py` |
| Flush lifecycle (compose → gate → append/marker), give-up, sweep | `lore_curator/chapter_flush.py` |
| Buffer-and-flush heartbeat (Curator A pass) | `lore_curator/session_curator.py` |
| Buffer storage + sidecar state machine | `lore_curator/buffer_store.py` |
| Note creation from a buffer (heartbeat + flush both call this) | `lore_curator/session_note.py` |
| Frontmatter-only hygiene passes | `lore_curator/hygiene.py` |
| Briefing gather (read-only) | `lore_core/briefing/gather.py` |
| Repo ADR/PRD pull (filesystem side) | `lore_core/repo_docs.py` |
| MCP server (tool dispatch) | `lore_mcp/server.py` |
| SessionStart / PreCompact banner + hooks | `lore_cli/hooks.py` |
| Freshness classification | `lore_core/freshness.py` |
| Vault/wiki/scope resolution | `lore_core/scope_resolver.py`, `lore_core/state/` |
