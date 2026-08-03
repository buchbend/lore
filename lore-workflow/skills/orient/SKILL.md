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

### 2. Pull the context pack, then fan out only what's thin
Before spawning anything, pull the deterministic context pack **once, up front**:
`lore_context_pack` (cold-start-safe — an empty repo/vault returns a well-formed empty pack,
never an error) plus `lore_repo_docs_list` / `lore_repo_docs_fetch` for the full ADR/PRD
listing and any body worth reading in full. Two facets are served straight from that pull, no
subagent:
- **Docs & decisions** — the pack's `adr` / `prd` entries (already linked to the focus
  issue/epic), CONTEXT.md / glossary read directly, `lore_repo_docs_list` for anything the
  pack's focus filter missed.
- **Prior art** — the pack's `sessions` (recent related session notes) and `epic_state`
  (linked issue/epic status) as the trace of related work.

Spawn a facet's `Explore` subagent only when the pack came back thin or empty for it (no
matching ADR/PRD, no matching sessions) — it then searches beyond what the deterministic join
found. The remaining facets fan out unconditionally, as before:
- **Code map** — where this touches the codebase: key modules, interfaces, current behavior,
  relevant tests. Start from `lore codemap` (or the `lore_codemap` MCP tool) for a ranked,
  deterministic index instead of a blind directory walk.
- **Cross-repo / external** — only when the intent plausibly spans repos or downstream consumers.

Whatever subagents this leaves running go out concurrently, in a single message, at **mid-tier
(REQUIRED)**: resolve the tier to a concrete model with `lore tier resolve mid` and set each
spawn's model parameter to that resolution — see
[TIER-DELEGATION.md](../../TIER-DELEGATION.md) for the no-implicit-inherit rule this enforces.

Scale the fan-out to the ask: a small change may need a single Explore pass (or none, if the
pack already covers it); a broad feature warrants all facets.

### 3. Reflect back
Present a tight brief in the conversation — **one screen, ~300 words, hard cap**. Order it so
what the user must react to comes first:
- **What we're actually deciding** — the crux, and the real choices ahead.
- **Open questions & assumptions** — the unknowns, and the assumptions you're running on.
- **What I understand you want** — one short paragraph, in the project's domain language.
- **Relevant landscape** — at most five bullets, each a fact that constrains a decision (an
  ADR that forbids an option, a module that already does half the work). Everything else you
  learned stays in your context; close with "ask for the long version" instead of printing it.
- **Tentative scope** — in / out, marked provisional.

The cap governs the printed brief, never the homework behind it — explore fully, report
selectively.

### 4. Loop
Ask whether this matches. If the user corrects or adds input, re-orient with a *targeted*
re-explore — only the facets that moved, not a full re-run. Repeat until they confirm.

### 5. Handoff
On confirmation, carry the shared understanding into the grilling step — default to
`/lore-workflow:grilling` in its "grill with docs" mode (it aligns terminology against CONTEXT.md/ADRs, which the downstream
`/lore-workflow:to-epic` slices depend on). Say "grill me" for plain `/lore-workflow:grilling` instead when there is no domain model to align
against.

Chain: `/lore-workflow:orient` → `/lore-workflow:grilling` → `/lore-workflow:to-epic` → `/lore-workflow:orchestrate-epic`.

Lighter rungs exist beside the chain: a single change that is clear-ish but unwritten
takes `/lore-workflow:brief`; a written, clear issue takes `/lore-workflow:implement-issue`.
Reserve this chain for multi-feature or unsettled work.
