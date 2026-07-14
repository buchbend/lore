---
name: lore-workflow:implement-issue
description: Fast path for one well-understood GitHub issue — read the issue and the code map, clarify only if needed, then TDD it on one branch to one PR with the ADR and docs gates applied. Use when the user points at a single clear issue and wants it implemented directly, not run through the whole epic chain.
---

# Implement Issue

A fast path for a single, well-understood GitHub issue. It is a **second track
beside the epic chain, not a new chain phase**: the chain
(`seed-epic → orient → grilling → to-epic → orchestrate-epic → document-epic`)
is the right weight for a shaped body of work, but far too heavy for one small,
clear change. Bypassing the workflow for that change is the other trap — it drops
exactly the discipline the workflow exists to keep (test-first development, the
architectural-decision check, the docs update). This skill keeps that discipline
at the weight of a single issue.

It **inherits the workflow invariants**: **strict TDD**, **ruff** clean, and
**never merge on red**. What it deliberately drops for the small track is the
fan-out and the multi-PR integration — one issue, one branch, one PR — and the
human stays present, so **merging stays with the user**.

## When to use

Reach for this when the user hands you a single GitHub issue they wrote and the
change is small and clear enough that spinning up the full epic chain would be
pure overhead. If the work is really several features, or the shape is still
unsettled, it belongs on the chain (`orient` → `grilling` → `to-epic`),
not here. If the task is clear-ish but only in the user's head — no written
issue yet — [`/lore-workflow:brief`](../brief/SKILL.md) is the rung in
between: it aligns in one exchange and can hand back to this skill.

## Flow

### 1. Read the issue and the code map

Read the GitHub issue the user wrote, then run `lore codemap` (or the
`lore_codemap` MCP tool) to locate the symbols and files the change
touches. That is the whole intake — **no exploration fan-out**. The code map is a
ranked navigation index built for exactly this: find the symbol, open the cited
file and line. Spinning up parallel explorer subagents for a single clear issue is
the epic-chain weight this track exists to avoid.

### 2. Clarify only if needed

A gate, not an interview. If the issue's intent or acceptance criteria are
**ambiguous**, ask the user **at most three targeted questions** and wait for the
answers. If the issue is already clear, proceed with **no interview at all**.

This is explicitly **not a grilling**. `grilling` stress-tests an unshaped plan
one relentless question at a time; that is the wrong tool for an issue the user
has already written down. Three questions is a ceiling for genuine ambiguity, not
a quota to fill — when in doubt, prefer to proceed.

### 3. Implement with `/lore-workflow:tdd`

Create one branch, `feat/<issue>-slug`, off the **detected target branch** —
`develop` if that branch exists on the remote, else the repo default (`main`);
never assume it. Implement the change with [`/lore-workflow:tdd`](../tdd/SKILL.md): the strict
red → green → refactor loop, one behavior at a time, tests before code. Everything
lands in **one PR** on that branch.

### 4. ADR gate

Apply `domain-modeling`'s three [ADR](../domain-modeling/ADR-FORMAT.md) criteria to
the decision the change embodies:

1. **Hard to reverse** — the cost of changing your mind later is meaningful.
2. **Surprising without context** — a future reader will wonder "why this way?".
3. **A real trade-off** — there were genuine alternatives and you picked one for
   specific reasons.

If **all three** hold, draft the ADR at `docs/adr/NNNN-kebab.md` in the **same
PR**. If any one is missing, **skip the ADR silently** — no note, no placeholder.
(See [`domain-modeling`](../domain-modeling/SKILL.md).)

### 5. Docs — Diátaxis pass

Run [`document-epic`](../document-epic/SKILL.md)'s classification over the
change's diff using the `lore_workflow.diataxis` module (part of the `lore`
package this plugin depends on): `classify_changeset(changes)` maps each changed
path to its Diátaxis **quadrant** (tutorial / how-to / reference / explanation) or
to "skip". Update the touched quadrants in the **same PR** — for reference, that
means docstrings plus the autosummary/toctree wiring, not hand-written prose.
`classify_changeset` never assigns a quadrant to `docs/prd/` or `docs/adr/`;
leave those alone.

### 6. Review, open the PR, report

Run **one review pass** with `code-review` at the **mid tier** by default — this is
the small track, so the review is lighter than the epic chain's strong-tier
crosscheck; escalate a genuinely architectural change to a stronger tier by
judgement. Address the findings, keep the PR green, then open it linking the issue
it closes and report back.

**Merging stays with the user.** They are present on this track, so the skill opens
the PR and stops there — it does not self-merge. **Never merge on red.**

## Relationship to the chain

This track and the epic chain solve different problems and do not compose: one
clear issue takes this fast path; a shaped, multi-feature body of work takes the
chain. See the repo's own conventions doc, if any, for the canonical chain and
the artifact homes this skill writes into (`docs/adr/`, the Diátaxis `docs/`).
