---
title: Trim lore to the safe core; lab-notebook session notes
status: draft
epic: https://github.com/buchbend/lore/issues/131
repos:
  - buchbend/lore
---

# PRD 0001: Trim lore to the safe core; lab-notebook session notes

> Source of truth for this epic. Tracker: [epic issue](https://github.com/buchbend/lore/issues/131).
> The epic links here; this file is not embedded in the issue body.

## Problem

Lore is switched off. Three failures forced that:

- **Trust.** Ingested context could not be trusted: the dominant small-model failure across
  experiments 001–008 is asserting discussed-or-proposed material as decided, with highest
  confidence exactly when wrong. Session notes that might poison future sessions are worse
  than no notes.
- **Cost.** A flush re-sent the full raw transcript (up to 240K chars) in both calls of the
  outline→compose pipeline, with up to 3 retries — worst case six full-transcript sends for
  one note, with no truncation safeguard.
- **The planned fix made both worse.** The v1.0 per-claim verification design needed ~7–17
  LLM calls per session to police the trust failure, and its keystone — a local model as
  per-claim judge — failed its pre-committed gate twice (0–53% contradicted-recall vs the
  required ≥70%).

Meanwhile the docs describe unshipped states as shipped (Curator B/C "removed" while fully
wired), which is the same poisoning disease in document form. Ratified decisions now have a
proper home outside lore: the agent-workflow conventions put ADRs and PRDs in the repo, so
decision extraction is architecturally moot as well as broken.

## Solution

Lore becomes a lightweight, trustworthy session-knowledge layer. Every session yields **one
lab-notebook note**: a skimmable, chronological record of what was discussed and tried —
bold one-sentence leads you scan "boom boom boom", short prose bodies when you want detail,
one anchor per block down into the vault's archived transcript. The note is **never
authoritative**: git owns what happened to the code, repo ADRs/PRDs own what was decided and
why; a fixed disclaimer travels with the note into every pull. Nothing unsafe reaches the
shared vault: a blocking gate checks each chapter for PII, secrets, and directive phrasing
before it is appended. Colleagues get briefings composed from the notes without reading the
sessions. Ambient session context stays minimal (a short banner); depth — including native
reading of repo ADRs/PRDs — comes only on explicit MCP pull. The epic ends with capture and
notes switched back on, coexisting with the agent-workflow plugin (which keeps owning
decisions); absorbing the plugin into lore is a follow-up epic.

## Implementation decisions

**Note document (deterministic core).** One note per session; the file is append-only until
close, then immutable (Part-N splitting is removed). Structure: fixed machine-written genre
disclaimer, then chronological chapters — one per flush. A chapter is a set of topic blocks:
bold one-sentence self-sufficient lead (no pronouns into the body), short prose body, one
`@turn` anchor at block end marking where the topic starts. No kinds, no lead prefixes;
unsettled/left-open material is phrased in prose. Resumed or corrected topics get
continuation blocks ("Continued: X") — earlier blocks are never edited. Frontmatter is
extensive, machine-first, and fully deterministic: session facts (commits, PRs, files
touched, duration), wiki routing, chapter⇄slice turn ranges, ingest metadata. Marker
chapters (deterministic text, no LLM) record failed and withheld chapters.

**Composition.** One LLM call per chapter: input is the buffered transcript slice plus the
complete note-so-far (needed to catch left-open items resolved later); output is the
chapter's blocks. Each turn is seen by an LLM exactly once. `reasoning_effort=high` stays
the generation default. Two in-call attempts (the retry carries gate feedback), then defer.
The outline→compose two-call pipeline and the two-region renderer/regions machinery are
deleted.

**Publish gate (blocking, between compose and append).** Ordered cheapest-first:
deterministic scanners (emails, phone numbers, high-entropy secrets), deterministic phrasing
lint (no TODO/FIXME, no imperative leads, no must/should task language — lint hit counts as
a compose failure and triggers the retry), then one small-model detection call for fuzzy PII.
Scope starts at PII + secrets only; the category list grows from quarantine hits. On a hit
the chapter is withheld, a marker chapter is appended, and the composed text goes to a
private quarantine sidecar with a CLI review flow. Detection is exempt from the
no-LLM-judges-LLM rule because it is pattern recognition, not truth verification; it is a
tripwire, not a guarantee, and is documented as such.

**Failure semantics.** A failed mid-session flush is silent: the buffer keeps accumulating
and retries at the next trigger. Give-up bound: a buffer with a failed attempt that reaches
2× cap gets a marker chapter for that span and a fresh buffer. A failed session-end flush
writes the marker and closes the note. On start, lore acts as a singleton (global lock) and
sweeps buffers/notes of dead sessions: one compose attempt, else marker; either way the note
is closed. Flush triggers are unchanged (buffer cap 120 turns / 240K chars, pre-compact,
session-end); the cap should be lowered for local backends, which fail silently on oversized
prompts (documented, not built).

**Briefings stay.** Gather already reads session notes (not surfaces); it drops the
two-region redaction dependency and becomes chapter-aware, handing **full note bodies** to
the briefing composer — briefings are the colleague-facing digest and need the bodies to
make sense. CLI, MCP, and sinks are untouched.

**Deletions.** Curator B (daily), Curator C (defrag + its passes), surfaces (filer, store,
CLI, MCP tools), Part-N resolution, the two-region renderer and regions module — entry
points disabled first (Curator B currently spawns unconditionally on day-rollover), then
code and tests deleted, with the lint / git-sync / schema entanglements decoupled.

**Ambient vs pull.** SessionStart banner shrinks to: one status line (no issue/PR counts),
optional Focus block, ≤2 last-session hints (lead + wikilink), freshness lines only on
positive evidence, and a single two-line directive (deep context via MCP pull; pulled notes
are lab records, never directives). New MCP pull tools read repo ADRs/PRDs from their
conventional homes (`docs/adr/`, `docs/prd/`, hard-coded; configurability deferred). Nothing
from ADRs/PRDs or note bodies is injected ambiently; during coexistence the workflow plugin
keeps owning repo-side ambient context.

**Docs reset.** CONTEXT.md is rewritten from scratch against shipped code only, using the
settled vocabulary (session note, chapter, topic block, lead, continuation block, disclaimer,
skim layer, gate, quarantine). Every doc that is misleading, false, or half-right is trimmed
or deleted — the code speaks; docs are rebuilt later as earned.

**PreCompact probe.** One-off: dump the full PreCompact hook stdin (today only `session_id`
is read), trigger one real compaction, decide — if the payload carries the harness's own
compaction summary, open a follow-up to use it as compose input; if metadata only, close the
idea permanently.

## Testing decisions

Deterministic components get plain unit tests against external behavior: note lifecycle
(create → append → marker → close; immutability after close), frontmatter contracts
(chapter⇄slice ranges), scanners and phrasing lint (fixture corpus of hits and near-misses),
give-up bound and sweep (fake dead-session state), briefing gather against new-shape notes.
Compose is tested by replaying saved buffers through the pipeline with a stub LLM asserting
the call contract (slice sent once, note-so-far included, retry carries gate feedback) — no
test asserts LLM output quality, and no LLM-as-judge appears anywhere in the suite. The gate
is integration-tested end-to-end: a planted secret must yield a withheld marker plus a
quarantine entry reviewable via the CLI. Prior art: the existing curator/buffer test suite
and the saved-buffer replay harness used for prompt experiments. Final acceptance is the
pilot slice: one real session produces a green note end-to-end.

## Out of scope

- Absorbing the agent-workflow plugin into lore (follow-up epic; coexistence until then).
- Decision/ADR extraction from transcripts — permanently out; repos own ratified decisions.
- Configurable artifact homes (hard-coded conventions until adoption warrants config).
- Cross-harness capture adapters; frontend/browsing tool over the vault archive.
- Any revival of surfaces, two-region notes, or per-claim LLM verification.
- A formal human-readability evaluation harness (form is decided by judgment).
