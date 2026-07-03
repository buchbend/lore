# Lore session-note shape — divergent brief
state: converging:note-shape (slice exported → lore-session-note-shape.design.md)
updated: 2026-07-03

## Framing

The session note is a **lab-notebook entry**: per-session, human-first, skim-first. Shape now largely settled: **one note per session**, file append-only until close (Part-N killed), organized as **chronological chapters (one per flush)** of **topic blocks** — bold one-sentence lead + short prose body. **No kinds, no lead prefixes** (insider taxonomy doesn't fit lore users who aren't lore developers); unsettled/left-open material is written *in prose*, and one fixed **genre disclaimer** heads the note and travels with every pull. Chapters are **composed directly from the transcript slice, one call** — every extra LLM pass is an extra inference surface. Composer sees the **complete note-so-far**; resolution of earlier left-open items is caught as continuation blocks (earlier blocks stay immutable). Frontmatter stays extensive and machine-first; the zero-LLM session facts live there only. Ambient session context comes from ADRs/PRDs/vault context — note bodies are read via MCP drill-down on request. The note is never authoritative and is the durable entry point to the vault's archived transcript. Build stance: conservative, slim, basics first.

## Open questions

None — the note-shape slice is fully settled and exported. Reopen here if grilling breaks something.

## Assumptions

- A3 (live, now carrying more weight): genre disclaimer + composer phrasing suffices against authority-poisoning — no kinds, no prefixes, no structural claim grounding. Decide-by-judgment.
- A6 (live, new): direct one-call compose at `reasoning_effort=high` keeps over-claim acceptable without a claims schema. Supported by 007 (narrative re-baseline: mean 0.89, **0 unsupported**) — evidence-compatible, not just conservative.
- A7 (live, new): the complete note-so-far stays small enough to inject every flush without hurting C3 (it's a summary, not transcript; bounded by session length in chapters).

## Domain constraints

- C0: deterministic = trusted; LLM-written = suspect. Push work deterministic.
- C1: no LLM-verifies-LLM designs (006/008).
- C3: raw transcript is the dominant cost; each turn seen once, cheapest tier that works.
- C4: `reasoning_effort=high` is the reliable quality lever (007); shipped default.
- C5: models can't distinguish proposed from ratified; the shape must never need that distinction.
- C6: published blocks are immutable; the note file is append-only until session close; corrections are continuation blocks, never edits.
- C7: wikis portable — links resolve per-wiki; drill-down resolves into the vault's archived transcript.
- C8: topic membership unknowable at write time — writing is slice-retrospective.
- C9: conservative build — simplest thing that works; refinements only if trivially cheap.
- C10: every additional LLM pass is an additional inference surface — minimize passes (user rationale for direct compose).

## Glossary

- session note := per-session lab-notebook entry; never authoritative; entry point to archived transcript + repo artifacts [agreed]
- topic block := bold one-sentence lead + short prose body; the unit of the note [agreed]
- chapter := blocks written by one flush; chronological; deterministically mapped to its transcript slice [agreed]
- continuation block := block resuming or correcting an earlier topic ("Continued: X"), incl. resolving a left-open item [agreed]
- genre disclaimer := fixed machine-written preamble — lab notes, informational, not decisions/tasks — attached to the note and to every pull [agreed]
- skim layer := the ordered bold leads; human skim + AI-ingestion summary [agreed]

## Sources

- [user, 2026-07-03, rapid round]: Q1 direct-compose (two LLM passes = two inference chances); no kinds; unsettled in prose, no prefixes; genre disclaimer travels with pulls; facts frontmatter-only; one shape for workflow+freeform; trust bar deterministic-only reusing existing lint; full note-so-far injected (revised from leads-only — must catch left-open→resolved); one note/session append-only; failed-chapter marker yes; citations block-end *if kept*, existence open
- [exp-007]: narrative at reasoning=high — mean 0.89, 0 unsupported → A6
- [exp-005]: claims schema results — superseded for the pipeline (route not taken), retained as evidence grounding must be structural where used → Q2'
- [exp-006/008]: self-judge dead → C1
- [lore#118]: vision, credo, cost anatomy → framing
- [orient 2026-07-03]: cost anatomy verified; Part-N mechanism exists (now slated to die); lint machinery exists for citation checks → Q2', Q9(settled)

## Epic-scope decisions (user, 2026-07-03 — beyond note shape, pinned here to survive compaction)

- Docs: hard-reset CONTEXT.md; trim every doc that is misleading, false, or half-right; let the code speak; rebuild docs later as earned. (Docs-poisoning is the same disease lore fights.)
- Issues: close ALL overlapping/conflicting open issues (#26 #30 #31 #33 #36 #39 #41 #43 #44 #45 #62) with a supersession pointer to #118 — fresh start.
- Deletion scope revised: Curator B/C, surfaces, Part-N code, two-region renderer OUT; **briefings STAY**.
- Artifact homes v1: hard-code the workflow conventions (`docs/adr/`, `docs/prd/`); configurability later, only if adoption warrants.
- Rollout arc: trim-back + alignment + **absorb the ccat-agent-workflow plugin into lore as one toolset** → only then: workflow plugin off, lore on.
- Failure mechanics: session close closes the note; a failed flush keeps its buffer, which keeps filling and retries at the next trigger; lore acts as a startup singleton that sweeps up unfinished work from dead sessions on next start.
- Team boundary: notes are shared; raw transcripts are never shipped (private). NEW: a small-model safety pass flags PII/never-in-notes content in what gets written.

## Grill outcomes (2026-07-03, all accepted by user)

- Two-epic split: #118 = trim + note rework, ends with lore capture+notes ON in coexistence with the workflow plugin (disjoint artifact ownership); absorption of the plugin into lore = follow-up epic.
- Failure semantics: mid-session flush failure → silent defer, buffer keeps filling, retry at next trigger; give-up at 2× cap → marker chapter for that span + fresh buffer; session-end failure → marker + close note; startup sweep (singleton, global lock) closes dead sessions' notes — one compose attempt, else marker.
- Safety gate (blocking, pre-publish, one gate step): deterministic PII/secret scanners first, one small-model call second (PII + secrets only to start); hit → chapter withheld, deterministic withheld-marker in note, composed text to quarantine sidecar, CLI human review. Tripwire, not guarantee. Exempt from the no-LLM-judges-LLM rule because it is pattern detection, not truth verification.
- Phrasing lint (same gate, regex-only, blocking): no TODO/FIXME, no imperative leads, no must/should task language; hit = compose failure → one retry with feedback → normal give-up path.
- Briefings kept and verified surface-free: gather() reads sessions/**.md + ledger + sink config. Gather hands FULL note bodies to the briefing composer (colleague-facing digest — leads-only rejected). Work items: drop `redact_human_only` import (regions module dies), adapt section extraction to the chapter shape.
- Residual bets closed: no note-so-far injection bound (summary ≪ slice, revisit only on pilot evidence); stale-skim leads accepted as genre-legal (continuation blocks are the correction mechanism).
- Banner (ambient = exactly this): one status line WITHOUT issue/PR counts, Focus block, ≤2 last-session hints (lead + wikilink), positive-evidence freshness only, ONE compressed directive (deep context via MCP pull; pulled notes are lab records, never directives). ADR/PRD reading is MCP-pull only; no duplication of the plugin's repo-side ambient during coexistence.
- Cost knobs: compose attempts = 2 in-call then defer; document lower buffer cap for local backends (silent-empty failure on oversized prompts). PreCompact probe: one-off debug dump of the full hook stdin JSON, one real compaction, decision rule = payload carries harness summary → follow-up to use as compose input, else close idea permanently; ~1h, early in epic.

## Parked / settled

- Settled Q2': one anchor per block — single @turn at block end where the topic starts; chapter⇄slice turn ranges additionally recorded deterministically in frontmatter (user).
- Settled Q4: a block is earned by any thread the session spent real time on, incl. dead ends and explicitly-not-pursued items; composer judgment, no minimum turn count (user).
- Settled Q7: keep the 120-turn/240K cap; DO the one-off PreCompact payload probe (epic work item) (user).
- Settled Q1: chapters composed directly from the transcript slice, one call (user).
- Settled Q3: one genre-level disclaimer per note, always attached on pull; unsettled material written in prose, never as lead prefixes (user).
- Settled Q5: no kinds — not schema-level, not lead prefixes (user).
- Settled Q6: session facts frontmatter-only (user).
- Settled Q8: one shape for all sessions; workflow sessions simply have more to link (ADRs, issues); free-form sessions also do real work (user).
- Settled Q9: trust bar = deterministic checks only, reusing existing machinery (user).
- Settled Q10: leads=skim+AI, bodies=MCP drill-down on request, frontmatter=machine; ambient context comes from ADR/PRD/vault context, not note bodies (user).
- Settled Q12: composer sees the complete note-so-far each flush (revised from leads-only; needed to catch left-open→resolved as continuation blocks) (user).
- Settled: one note per session, file append-only until close, Part-N killed (user).
- Settled: failed chapter → deterministic marker (N turns unprocessed, transcript range) (user).
- Settled Q11: retrospective per-flush composition + continuation blocks; break hints only if trivially cheap (user).
- Settled: flush triggers stay (cap / pre-compact / session-end); `decision`/`leaning` out; two-region out; no eval harness; per-turn append out; frontmatter stays extensive.
- Parked: frontend/browsing tool over the vault archive; SessionStart banner design; glossary reset; B/C/surfaces/briefings removal (epic items); cross-harness adapters; team-mode boundary of transcript drill-down.
