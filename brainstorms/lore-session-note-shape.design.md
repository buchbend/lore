# Lore session note — settled shape (design export)

Exported 2026-07-03 from `lore-session-note-shape.md` (divergent brief). All items below were
settled explicitly by the user; this file is the note-shape input to grilling and to epic
buchbend/lore#118. It is a design description, not a build plan.

## The note at a glance (illustrative skeleton)

```markdown
---
# extensive frontmatter, machine-first, all deterministic:
# session facts (commits, PRs, files touched, duration), wiki routing,
# chapter⇄slice turn ranges, ingest metadata, summary field
---
> Lab notes: this records what was discussed and tried in one working session.
> Nothing here is a decision, requirement, or task. Ratified decisions live in
> the repo (docs/adr, docs/prd); code changes live in git.

## Chapter 1        <!-- flush 1 · turns 1–74 -->
- **Narrowed the flaky login test to a session-cookie race.** …short prose body;
  dead ends and left-open points phrased in prose, never as directives. [@12]
- **Discussed moving retry logic into the client; no conclusion reached.** [@31]

## Chapter 2        <!-- flush 2 (pre-compact) · turns 75–140 -->
- **Continued: session-cookie race — resolved by pinning the clock in tests.** [@88]
```

## Settled decisions

1. **Genre & authority.** Lab-notebook entry; never authoritative. One fixed, machine-written
   genre disclaimer heads the note and travels with every MCP pull. No kinds, no lead
   prefixes (taxonomy is developer jargon); unsettled/left-open material is written in prose.
2. **Structure.** One note per session; the file is append-only until session close (Part-N
   mechanism dies). Chapters = flushes (existing triggers unchanged: 120-turn/240K-char cap,
   pre-compact, session-end). Blocks = bold one-sentence lead + short prose body. Published
   blocks are immutable; resumed or corrected topics get continuation blocks ("Continued: X"),
   including left-open items resolved later in the session.
3. **Composition.** One LLM call per chapter, directly over the transcript slice — no claims
   stage, no outline stage (each extra pass is an extra inference surface; C10).
   `reasoning_effort=high` stays the generation default. The composer receives the complete
   note-so-far plus the new slice. Leads must be self-sufficient (no pronouns into the body) —
   they are simultaneously the human skim layer, the AI-ingestion summary, and continuation
   context.
4. **Grounding & trust.** One anchor per block: a single `@turn` at block end marking where the
   topic starts. Chapter⇄slice turn ranges are recorded deterministically in frontmatter
   (coarse drill-down exists even without anchors). Verification is deterministic only:
   anchor resolves within the chapter's slice, format lint, frontmatter schema. No LLM
   verification anywhere (self-judging is a proven dead end).
5. **Failure honesty.** If a chapter's compose fails after retries, a deterministic marker
   chapter is appended: "chapter not written: N turns unprocessed, turns A–B in archive."
6. **Readers.** Leads = skim + AI layer; bodies = human drill-down and MCP reads on request;
   frontmatter = machine. Ambient session context comes from ADRs/PRDs/vault context — never
   from note bodies pushed uninvited.
7. **Block floor.** Any thread the session spent real time on earns a block — including dead
   ends and explicitly-not-pursued items. Composer judgment; no minimum turn count.
8. **One shape for all sessions.** Workflow sessions simply have more artifacts to link
   (ADRs, PRDs, issues); free-form sessions produce the same shape.
9. **Deterministic session facts** (commits, PRs, files touched, duration) live in frontmatter
   only — never re-enumerated in the body.
10. **Entry point.** The note links down into the vault's archived full transcript (the vault
    retains sessions longer than the harness); anchors and chapter ranges resolve there,
    per-wiki (portability preserved).

## Approved follow-up

- One-off empirical probe of the PreCompact hook payload (lore currently discards it unread) —
  if the harness ships usable pre-condensed content, it becomes free composer input.

## Explicitly rejected

claims[] pipeline stage · kinds/lead prefixes · two-region gating · per-turn append ·
online LLM topic segmentation · LLM self-judging · formal human-feel eval harness · Part-N notes.

## Known bets to stress-test (grill these)

- **A3:** genre disclaimer + composer phrasing alone prevents authority-poisoning — there is no
  structural grounding beyond one anchor per block.
- **A6:** one-call direct compose at high reasoning keeps over-claim acceptable (supported by
  experiment 007: narrative re-baseline mean 0.89, 0 unsupported).
- **A7:** injecting the complete note-so-far each flush stays cheap (bounded: it's a summary,
  not transcript).
- Stale-skim risk: a resolved left-open item stays visible in an early chapter's lead
  (immutable); the correction lives in a later continuation block. Acceptable by genre —
  confirm under grilling.
