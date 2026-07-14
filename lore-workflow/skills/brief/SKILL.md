---
name: lore-workflow:brief
description: Middle-weight rung between implement-issue and the epic chain — pack-only
  orientation, one reflected brief with recommended answers, document gates, then handoff
  to implement-issue or tdd. Use when the task is clear-ish in the user's head but not
  written down, medium-sized, and needs alignment without a full grilling. Triggers on
  "brief me", "brief this", or "/lore-workflow:brief".
---

# Brief

You are briefing: compress `orient` + `grilling` into **one bounded exchange**, then hand
off. The task is clear-ish in the user's head but not written down — too big to skip
alignment, too small for the epic chain's fan-out and interview.

Routing rule (which rung a task belongs on):

- **Written & clear issue** → `/lore-workflow:implement-issue`.
- **In the user's head, single change** → this skill.
- **Multi-feature or unsettled** → the chain (`/lore-workflow:orient` → `grilling` → `to-epic`).

## Process

### 1. Pack-only orientation — no fan-out, ever

Pull `lore_context_pack` once and `lore_codemap` (or `lore codemap`) once. That is the whole
intake. **Never spawn Explore subagents** — if the pack comes back thin for some facet, say
so in the brief instead of searching wider. The context pack was built precisely so a
deterministic pull replaces the fan-out on known ground; the fan-out weight is what this
rung exists to avoid.

### 2. One reflected brief, questions embedded

Present a single message:

- **What I understand you want** — restated in the project's domain language.
- **Constraints that bear on it** — relevant ADRs, prior sessions, code landscape from the
  pack. Name explicitly any facet where the pack was thin.
- **Provisional scope** — in / out.
- **Questions (max 3–5), each with your recommended answer pre-filled.**

The user replies once — "yes to your recs except #3" — and that *is* the alignment. No
one-question-at-a-time loop. Ask a second round only if an answer genuinely forks the
design; this is explicitly **not a grilling**.

### 3. Escalate if it turns out to be chain-weight

If while briefing you discover the work is really multi-feature or the shape is unsettled,
stop and say so: "this is chain-weight — run `/lore-workflow:orient`". The brief you already
wrote seeds orient's step 1; nothing is wasted.

### 4. Document gate, not document ritual

Reuse the existing gates verbatim — no new document types:

- **ADR** — apply `domain-modeling`'s three criteria (hard to reverse / surprising without
  context / a real trade-off) to the decision the brief lands on. All three hold → draft the
  ADR at `docs/adr/NNNN-kebab.md` as part of the downstream work. Any one missing → skip
  silently, no placeholder. (See [`domain-modeling`](../domain-modeling/SKILL.md).)
- **Glossary / CONTEXT.md** — touch only if the brief actually coined a *new* domain term.
- **No PRD, no epic tracker.** Those belong to the chain.
- **No brief file.** Lore's auto session capture already records the exchange; a persisted
  brief would duplicate what session notes do.

### 5. Handoff

On confirmation, pick one:

- **(a) File an issue and run the fast track** — `gh issue create` capturing the agreed
  brief (intent + acceptance criteria), then invoke
  [`/lore-workflow:implement-issue`](../implement-issue/SKILL.md) on it. Default for
  anything that benefits from a durable tracker entry.
- **(b) Implement directly with [`/lore-workflow:tdd`](../tdd/SKILL.md)** — when even an
  issue is ceremony. The workflow invariants still apply: strict TDD, ruff clean, never
  merge on red, ADR gate (above) and the Diátaxis docs pass from
  [`implement-issue`](../implement-issue/SKILL.md) step 5, one branch, one PR, merging
  stays with the user.

Either way the consistent-documents guarantee survives, because the ADR and Diátaxis gates
run downstream on the implementing track.
