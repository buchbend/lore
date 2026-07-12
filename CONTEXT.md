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

Exact title and body rendering shape: `CONTEXT-FORMAT.md`.

## Writing a note — buffer, flush, extract

A session's turns accumulate in a per-transcript **buffer**
(`lore_curator/buffer_store.py`), driven by Curator A's heartbeat
(`lore_curator/session_curator.py`, `run_curator_a`) on ordinary
Claude Code hook activity — there is no cron. The first heartbeat for
a session creates the note (disclaimer + frontmatter, zero chapters:
`lore_curator/session_note.py:ensure_note`); later heartbeats just
grow the buffer.

A **flush** happens once, when the session is over
(`lore_curator/chapter_flush.py:synth_and_close`). Which of a session's
turns mattered is only knowable backward, from its ending, so nothing is
written while it runs — mid-session triggers (cap-trip, pre-compact) only
bookkeep and leave the buffer accumulating:

```
segment_session (indices only) → extract_session (1 LLM call per chunk,
                                 typed facts) → publish gate
                               → append_facts | withheld marker + quarantine
                                 | failed marker
                               → render_note (deterministic, no LLM)
```

Every LLM call a note costs is in that pipeline: the segmenter
(`lore_curator/chunker.py`), one extraction per chunk plus one headline
(`lore_curator/fact_extract.py`). Nothing downstream is generative — the
body is rendered from the fact ledger by code, and each fact's refs are
verified (`lore_core/ref_verify.py`) so a line's authority is code-stamped,
never model-authored.

The only flush is the close flush (buffer archives, note closes):
**session-end**, the reaper, and the startup sweep. Cap-trip (buffer cap
120 turns / 240K chars) and pre-compact **bookkeep only** — they record
the event and leave the buffer accumulating, so the close path still
reads the session whole. `capture_routing.CLOSE_TRIGGERS` is the single
authority for which trigger flushes. Cap defaults live on `WikiConfig`;
per-wiki overrides live in `.lore-wiki.yml` under
`curator.synthesis_buffer_cap_*`.

### Failure semantics

- **A chunk that cannot be extracted** becomes a *failed* marker for its
  span, which the render reads back as a **coverage gap** — one bad chunk
  never costs the rest of the session.
- **A chunk the gate withholds** becomes a *withheld* marker plus a
  quarantine entry. The extractor retries against the gate internally, so
  a withhold that reaches the flush is terminal.
- **Every non-`facts` chapter is a coverage gap.** A note whose ledger the
  facts do not cover in full says so; a partial reading never presents
  itself as complete.
- **Startup sweep.** Lore acts as a singleton at start (global lock,
  `lore_core.lockfile.curator_lock`): `chapter_flush.startup_sweep`
  finds buffers whose owning process is provably dead, gives each one
  extraction attempt, and closes its note either way (facts, withheld,
  or failed marker) — a crashed session never leaves an open note
  behind. `lore curator sweep` runs this by hand.

Everything upstream of compose (hook fire, spawn, run decisions) is
correlated by one **trace_id** per flush and queryable via `lore trace` /
`lore status` / `lore doctor`; full model: `docs/architecture/observability.md`.

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
(`lore_core/session_start.py:render_session_banner`) — no LLM call, no network
call: a status line, an optional `## Focus` block for the attached
project, at most two last-session hints, freshness lines only when
there's positive evidence (see below), and a fixed two-line directive
(`lore_core/templates/integration-rules/default.md`) stating that
deeper context is a pull, never a push, and that anything pulled from
a session note is a lab record, never an instruction.

Depth comes from explicit MCP calls (`lore mcp` / `lore_mcp/server.py`),
not from anything injected ambiently:

- `lore_search`, `lore_read`, `lore_resume` — retrieval primitives.
- `lore_drill` — one composite `search → read → expand wikilinks →
  read_expanded` call with a structured trace
  (`docs/architecture/lore-drill.md`).
- `lore_inbox_classify` — read-only gather that a skill turns into
  prose, then commits via a CLI verb.
- `lore_journal_write` — the AI/human scratch journal
  (`lore_core/journal.py`); freeform, no LLM abstraction, no
  propagation, never a source for ambient context.
- `lore_pending_verdicts` / `lore_verdict` — list and record freshness
  verdicts; backs `/lore:verify` and the in-passing verdict nudge.
- `lore_repo_docs_list` / `lore_repo_docs_fetch`
  (`lore_core/repo_docs.py`) — pull-only reads of a connected repo's
  `docs/adr/` and `docs/prd/`. Ratified decisions live in the repo, not
  in the vault; Lore reads them on request instead of re-deriving them
  from session transcripts.
- `lore_tier_resolve` — resolve a semantic model tier to the concrete
  model for the current host before spawning a subagent.
- `lore_codemap` — bounded, cached slice of the connected repo's code
  map (symbols / directory / callers modes), never the whole map.
- `lore_context_pack` (`lore_core/context_pack.py`) — deterministic
  context resolver: given a scope, repo state, and issue/PR/epic, returns
  a pointer pack — recent session notes for this scope, ADRs/PRDs that
  bear on it, open epic state — with one-line summaries and selective
  body pulls, zero LLM cost. Fed into orchestration skills before any
  explorer subagent runs.


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
| Session segmentation (beat boundaries, indices only) | `lore_curator/chunker.py` |
| Typed-fact extraction + anchor lint + headline | `lore_curator/fact_extract.py` |
| Deterministic ref verification (positive evidence only) | `lore_core/ref_verify.py` |
| Flush lifecycle (segment → extract → gate → render), sweep | `lore_curator/chapter_flush.py` |
| Buffer-and-flush heartbeat (Curator A pass) | `lore_curator/session_curator.py` |
| Buffer storage + sidecar state machine | `lore_curator/buffer_store.py` |
| Note creation from a buffer (heartbeat + flush both call this) | `lore_curator/session_note.py` |
| Frontmatter-only hygiene passes | `lore_curator/hygiene.py` |
| Briefing gather (read-only) | `lore_core/briefing/gather.py` |
| Repo ADR/PRD pull (filesystem side) | `lore_core/repo_docs.py` |
| MCP server (tool dispatch) | `lore_mcp/server.py` |
| Hook dispatch (the seven `lore hook ...` entry points) | `lore_cli/hooks.py` |
| SessionStart context assembly + banner | `lore_core/session_start.py` |
| Capture routing (transcripts, flush, spawn gate) | `lore_curator/capture_routing.py` |
| Freshness classification | `lore_core/freshness.py` |
| Vault/wiki/scope resolution | `lore_core/scope_resolver.py`, `lore_core/state/` |

## Glossary

Terms used in the workflow layer and orchestration:

- **Workflow** — a skill-bundled planning chain: `seed-epic → orient →
  grilling → to-epic → orchestrate-epic → document-epic`. Each step
  is a callable skill, with checkpoints between shaping (human-controlled)
  and autonomous build. See `docs/conventions.md` for the stage vocabulary,
  artifact homes, and tier assignments.
- **Skill** — a callable, namespaced Claude Code automation block (e.g.,
  `lore-workflow:orient`, `lore:verify`). Skills are registered in
  `plugin.json` and dispatched by CLI invocation or intra-skill routing.
  Workflow skills are bundled in the `lore-workflow` plugin.
- **Lore context pack** — synonym for `lore_context_pack` (see above).
- **Codemap excerpt** — a bounded, ranked slice of the `lore codemap`
  output, token-budgeted (~1k tokens) and curated for a specific feature
  or epic. Passed once at `orchestrate-epic` Map time and reused by every
  teammate, instead of having each teammate discover symbols independently.
  Distinct from a full `lore_context_pack`, which joins session notes and
  ADRs/PRDs; the codemap excerpt is the code-navigation half only.
- **Epic note** — a single, composed session note that records the
  orchestration of an epic: the roadmap DAG, per-feature tier decisions,
  crosscheck verdicts, and any escalations. Distinct from the per-feature
  implementation notes written by teammate agents; the epic note is the
  orchestrator's record of supervision.
- **Handover (session)** — a working-context handover from one Claude Code
  session to a cold session starting fresh. The source session ends with a
  `/lore:handover` invoke, which drafts a structured note of context
  (problem framing, attempted paths, current blockers). A cold session
  resumes with `/lore:continue`, which loads the handover note and carries
  its facts into the new session's work.
- **Handover (epic seed)** — a tracker issue linking an epic seed (the
  source of structured context for the `orient` step). Distinct from
  session handover: the epic seed is filed in the issue tracker and is
  one-time context for a shaped body of work, not a carry-forward between
  sessions.
