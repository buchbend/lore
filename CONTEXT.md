# Lore — domain context

An AI-navigation map of Lore's domain. Every term below has one tight
meaning, matched against shipped code. Cross-check a claim here against
the module it points at before you rely on it. A term or a behavior
that no real symbol grounds does not belong here.

## Vault, wiki, scope

- **Vault** — the directory `$LORE_ROOT` points at. Contains one
  `wiki/` subdirectory plus `.lore/` for derived, host-local state.
- **Wiki** — a mounted knowledge store at `<vault>/wiki/<name>/`. Each
  wiki is its own git repo; a vault can host several. `lore wiki new`
  scaffolds `projects/`, `concepts/`, `decisions/`, `sessions/`,
  `inbox/`. Lore writes only the session notes automatically. A human
  or `/lore:inbox` writes the rest, when something is worth a
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
It is a **lab notebook**, not a source of truth. Git owns what
happened to the code. The connected repo's ADRs and PRDs own what was
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
- **Topic block** — one topic within a chapter. It holds a bold,
  one-sentence, self-sufficient **lead**, an optional short prose
  **body**, and exactly one `@N` **anchor**. The lead must stand alone:
  no pronoun reaches into the body. The anchor ends the block and
  points at the transcript turn where the topic started.
- **Continuation block** — a topic block that resumes or corrects a
  topic raised in an earlier chapter. Renders as `**Continued: <topic>**`
  instead of a fresh lead. Earlier blocks are never rewritten; a
  correction always arrives as a new block later in the note.
- **Skim layer** — the sequence of bold leads read top-to-bottom,
  ignoring the bodies. Every lead is self-sufficient, so the skim layer
  alone is a legible, boom-boom-boom summary of the whole session. The
  bodies hold the detail.
- **Marker chapter** — a chapter the *system* writes, not an LLM. Its
  deterministic text records that a chapter was **withheld** (the gate
  rejected it) or **failed** (composition never produced anything
  anchor-clean). See `MARKER_WITHHELD` / `MARKER_FAILED` and
  `append_marker_chapter` in `note_document.py`.

Frontmatter is machine-first and deterministic. It holds `note_status`
(`open`/`closed`), the `chapters` list (each entry's turn range and
kind), and a cumulative `SessionFacts` snapshot (commits, PRs, files
touched/read, duration). The snapshot only ever grows. Nothing in
frontmatter is re-narrated in the body.

Exact title and body rendering shape: `CONTEXT-FORMAT.md`.

## Writing a note — buffer, flush, extract

A session's turns accumulate in a per-transcript **buffer**
(`lore_curator/buffer_store.py`). Curator A's heartbeat
(`lore_curator/session_curator.py`, `run_curator_a`) drives the buffer
on ordinary Claude Code hook activity. There is no cron. The first
heartbeat for a session creates the note (disclaimer + frontmatter, zero
chapters:
`lore_curator/session_note.py:ensure_note`); later heartbeats just
grow the buffer.

A **flush** happens once, when the session is over
(`lore_curator/chapter_flush.py:synth_and_close`). Which turns of a
session mattered is only knowable backward, from its ending. Lore writes
nothing while the session runs. Mid-session triggers (cap-trip,
pre-compact) only bookkeep and leave the buffer accumulating:

```
segment_session (indices only) → extract_session (1 LLM call per chunk,
                                 typed facts) → publish gate
                               → append_facts | withheld marker + quarantine
                                 | failed marker
                               → render_note (deterministic, no LLM)
```

Every LLM call a note costs is in that pipeline: the segmenter
(`lore_curator/chunker.py`), one extraction per chunk plus one headline
(`lore_curator/fact_extract.py`). Nothing downstream is generative. Code
renders the body from the fact ledger. `lore_core/ref_verify.py` verifies
each fact's refs, so a line's authority is code-stamped, never
model-authored.

The only flush is the close flush (buffer archives, note closes):
**session-end**, the reaper, and the startup sweep. Cap-trip (buffer cap
120 turns / 240K chars) and pre-compact **bookkeep only**. Both record
the event and leave the buffer accumulating, so the close path still
reads the session whole. `capture_routing.CLOSE_TRIGGERS` is the single
authority for which trigger flushes. Cap defaults live on `WikiConfig`;
per-wiki overrides live in `.lore-wiki.yml` under
`curator.synthesis_buffer_cap_*`.

### Failure semantics

- **A chunk that cannot be extracted** becomes a *failed* marker for its
  span. The render reads that marker back as a **coverage gap**. One bad
  chunk never costs the rest of the session.
- **A chunk the gate withholds** becomes a *withheld* marker plus a
  quarantine entry. The extractor retries against the gate internally, so
  a withhold that reaches the flush is terminal.
- **Every non-`facts` chapter is a coverage gap.** A note whose ledger the
  facts do not cover in full says so; a partial reading never presents
  itself as complete.
- **Startup sweep.** Lore acts as a singleton at start, under the
  global lock `lore_core.lockfile.curator_lock`.
  `chapter_flush.startup_sweep` finds buffers whose owning process is
  provably dead. The sweep gives each buffer one extraction attempt,
  then closes its note either way (facts, withheld, or failed marker).
  A crashed session never leaves an open note behind.
  `lore curator sweep` runs the sweep by hand.

One **trace_id** per flush correlates everything upstream of compose:
hook fire, spawn, run decisions. `lore trace`, `lore status` and
`lore doctor` query that trace. Full model:
`docs/architecture/observability.md`.

## The publish gate + quarantine

Every composed chapter passes `lore_core/publish_gate.py:evaluate`
before it joins the shared note. The gate is the last check between an
LLM's output and the shared vault. The gate **fails closed**: any error
anywhere in the gate withholds rather than passes.

Cheapest-first, short-circuiting on the first hit:

1. **Deterministic scanners** — high-entropy secrets (via
   `lore_core.redaction`), email addresses, phone numbers.
2. **Deterministic phrasing lint** — TODO/FIXME, an imperative-verb
   bold lead, or must/should task language. The note is a past-tense
   lab record, never a directive; a lint hit counts as a compose
   failure and its feedback drives the retry.
3. **One small-model detection call** (`LlmPiiDetector`, cheapest
   tier) for fuzzy PII/secrets that slip the first two layers. The
   call is pattern recognition, not truth verification, so it is
   exempt from the no-LLM-judges-LLM rule. The call is a tripwire, not
   a guarantee. This file and the module docstring both say so.

On a withhold, `apply_withhold` runs two terminal side-effects. A
deterministic withheld-marker chapter goes into the note, with safe,
value-free reason text: the category, never the matched string. The
full composed text goes into the private **quarantine** sidecar
(`lore_core/quarantine.py`), one JSON file per entry under
`.lore/quarantine/`. That path sits inside the already-private
`.lore/` area and never reaches the shared wiki.
`lore quarantine list/show/clear/kill` is the reviewer's flow over that
sidecar. `list` never prints body content, because an entry may hold
the very secret that tripped the gate.

## Hygiene — the retained frontmatter-only curator

`lore curator [--wiki] [--apply]` takes no subcommand. The bare form is
distinct from `lore curator run`, the Curator A pass above. The bare
form runs deterministic, frontmatter-only passes over every wiki
(`lore_curator/hygiene.py`). The passes are supersession propagation
(`supersedes [[B]]` → `superseded_by: [[A]]` on B), `implements:`
back-link processing, and git-log date backfill. Another pass hints at
team mode once a solo wiki's git log shows multiple authors. Staleness
is a deliberate no-op here — see `lore_core/freshness.py` below.
Findings land in
`wiki/<name>/_review.md`; writes are mtime-guarded so a note open in
Obsidian is skipped rather than clobbered. `--apply` is required to
write; the default is a dry-run.

## Briefings

`lore_core/briefing/gather.py:gather()` is the read-only half of
`lore briefing`. It collects session notes filed since the last
briefing, tracked in a per-wiki ledger. It hands their **full note
bodies** — disclaimer plus every chronological chapter — to whatever
composes the briefing prose. There is no per-note redaction step.
Session notes carry nothing that isn't already safe to share, because
the publish gate cleared the text before it reached the note. Briefing
publish is manual (`lore briefing publish`, `lore briefing mark`); there
is no automatic daily trigger. See `docs/how-to/matrix-bot.md` for the
Matrix sink walkthrough.

## Ambient banner vs. MCP pull

SessionStart injects a deliberately small, deterministic banner
(`lore_core/session_start.py:render_session_banner`). The banner costs
no LLM call and no network call. It holds a status line, an optional
`## Focus` block for the attached project, and at most two last-session
hints. Freshness lines join the banner only when there is positive
evidence (see below). A fixed two-line directive closes the banner
(`lore_core/templates/integration-rules/default.md`). The directive
states that deeper context is a pull, never a push. It also states that
anything pulled from a session note is a lab record, never an
instruction.

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
- `lore_context_pack` (`lore_core/context_pack.py`) — a deterministic
  context resolver. It takes a scope, repo state, and an issue, PR or
  epic. It returns a pointer pack: recent session notes for that scope,
  ADRs and PRDs that bear on the scope, and open epic state. Each entry
  carries a one-line summary and a selective body pull. The resolver
  costs no LLM call. Orchestration skills read the pack before any
  explorer subagent runs.


## Retrieval substrate

Kept, general-purpose, and independent of the note-writing pipeline
above:

- **Freshness** (`lore_core/freshness.py`) — positive-evidence-only
  staleness. A note is flagged `stale-candidate` only for a named
  cause. The causes are an authored `status: stale` / `superseded_by` /
  `supersede_candidate*` marker, or membership in the orphan-link set.
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
- **Epic note** — a single, composed session note for the orchestration
  of an epic. The note records the roadmap DAG, per-feature tier
  decisions, crosscheck verdicts, and any escalations. Distinct from the
  per-feature implementation notes written by teammate agents; the epic
  note is the orchestrator's record of supervision.
- **Handover (session)** — a working-context handover from one Claude Code
  session to a cold session starting fresh. The source session ends with a
  `/lore:handover` invoke, which drafts a structured note of context
  (problem framing, attempted paths, current blockers). A cold session
  resumes with `/lore:continue`, which loads the handover note and carries
  its facts into the new session's work.
- **Handover (epic seed)** — a tracker issue linking an epic seed (the
  source of structured context for the `orient` step). The epic seed
  differs from a session handover. A team files the seed in the issue
  tracker. The seed is one-time context for a shaped body of work, not
  a carry-forward between sessions.
- **Writing rules** — the prose style an agent uses for issue text, PR
  descriptions, PR review comments, ADR context sections and design
  documents. Session notes stay out. The document holds sentence and
  vocabulary rules, EARS acceptance criteria, and the required section
  skeleton. Lore ships one default; a team overrides it whole-file with
  `<wiki>/style/writing-rules.md`.
  `lore style show writing-rules` resolves the two. The rules fix style,
  not terminology — terminology stays with the glossary.
  Overriding the lint means copying `styles/vale/` whole — the ini plus
  its `WritingRules/` rule directory — into `<wiki>/style/vale/`. Vale
  resolves `StylesPath` next to the ini, so an override that copies the
  ini alone exits 2 with "style 'WritingRules' does not exist on
  StylesPath". `lore style show issue-register` names the retired term and
  still resolves the document.
- **Change** — the unit an issue under the writing rules describes: one
  required-behaviour statement with its own acceptance criteria.
- **Batch issue** — an issue carrying several changes under one Context
  section, landing as one PR, with no ordering dependency between the
  changes. A change that needs its own context, or that must land before
  another, leaves the batch.
