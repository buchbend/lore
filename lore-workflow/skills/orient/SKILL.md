---
name: lore-workflow:orient
description: The first step of a piece of work — the session does its own homework, then
  reflects its understanding back for confirmation before any planning or grilling. Use at
  the start of a task, when the user says "orient", "get oriented", "what's your
  understanding", or wants the session to reflect back before planning.
---

# Orient

You are orienting: before any planning or grilling, do your own homework on what the user
asked, then reflect your understanding back for confirmation. You explore and restate; you do
not implement, and you do not fix scope unilaterally.

**Mode:** conversation only — nothing is persisted. The reflected understanding lives in the
chat and becomes the input to the next step.

## Process

### 1. Capture intent
Take the user's stated goal as the seed, verbatim. Don't expand it or jump to solutions yet.
If they pass an issue reference (e.g. an epic seed from `/lore-workflow:seed-epic`), fetch and read it
(`gh ... --json`) and treat its Intent + Findings as the seed.

### 2. Explore in parallel (subagents)
Fan out read-only `Explore` subagents concurrently — one per facet, in a single message — and
keep only their findings here (drop the file dumps). This fan-out runs at **mid-tier
(REQUIRED)**: resolve the tier to a concrete model with `lore tier resolve mid` and set each
spawn's model parameter to that resolution — see
[TIER-DELEGATION.md](../../TIER-DELEGATION.md) for the no-implicit-inherit rule this enforces.
Default facets:
- **Code map** — where this touches the codebase: key modules, interfaces, current behavior,
  relevant tests. Start from `lore codemap` (or the `lore_codemap` MCP tool) for a ranked,
  deterministic index instead of a blind directory walk.
- **Docs & decisions** — CONTEXT.md / glossary, `docs/adr`, relevant guides; what is already
  decided or constrained.
- **Prior art** — related issues/PRs and similar existing features.
- **Cross-repo / external** — only when the intent plausibly spans repos or downstream consumers.

Scale the fan-out to the ask: a small change may need a single Explore pass; a broad feature
warrants all facets.

### 3. Reflect back
Present a tight brief in the conversation:
- **What I understand you want** — restated in the project's domain language.
- **Relevant landscape** — the code + docs/ADR constraints and prior art that bear on it.
- **What we're actually deciding** — the crux, and the real choices ahead.
- **Open questions & assumptions** — the unknowns, and the assumptions you're running on.
- **Tentative scope** — in / out, marked provisional.

### 4. Loop
Ask whether this matches. If the user corrects or adds input, re-orient with a *targeted*
re-explore — only the facets that moved, not a full re-run. Repeat until they confirm.

### 5. Handoff
On confirmation, carry the shared understanding into the grilling step — default
`/lore-workflow:grill-with-docs` (it aligns terminology against CONTEXT.md/ADRs, which the downstream
`/lore-workflow:to-epic` slices depend on). Use `/lore-workflow:grill-me` instead when there is no domain model to align
against.

Chain: `/lore-workflow:orient` → `/lore-workflow:grill-with-docs` → `/lore-workflow:to-epic` → `/lore-workflow:orchestrate-epic`.
