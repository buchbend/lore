---
name: lore-workflow:seed-epic
description: At the end of a long session — often after /lore-workflow:orchestrate-epic surfaced follow-up
  work — capture this session's context into an epic seed, a GitHub tracker issue a fresh
  session can pick up cold and run /lore-workflow:orient on. Use when follow-up issues surfaced, the
  session is too long to continue cleanly, and you want to hand the discussion to a new
  session.
---

# Seed Epic

Capture this session's context into an **epic seed** — a tracker issue a fresh session can
orient on cold. The seed is a **discussion seed, not a spec**: it carries the intent and the
expensive-to-rebuild context, then the new session forms the epic via
`/lore-workflow:orient → /lore-workflow:grilling → /lore-workflow:to-epic → /lore-workflow:orchestrate-epic`. Do NOT pre-slice or pre-decide
scope — that is orient/grill/lore-workflow:to-epic's job.

**Tier note:** this step runs in the main session at frontier-tier — it is not delegated to a
subagent.

**When:** end of a session (typically after `/lore-workflow:orchestrate-epic`) where follow-up work surfaced,
the context is rich, but the session is too long to continue cleanly.

**Quality bar:** a cold session must be able to orient from the seed issue alone (plus the
repo). Capture what would otherwise have to be rediscovered.

## Process

### 1. Harvest
Gather the follow-up work that surfaced this session: new problems, deferred ideas, loose ends,
gotchas hit during implementation. Synthesize from context — don't interview.

### 2. Group
Cluster the threads into coherent seeds (one future epic each); unrelated threads → separate
seeds. Trivially small, well-understood follow-ups don't need the chain — note them for direct
filing instead. Confirm the grouping with the user before publishing.

### 3. Write the seed(s)
Nothing writes a session note since the compose pipeline retired, so freehand reconstruction is
the normal path: reconstruct Origin (the finished epic, merged PRs/commits) and Findings
(hard-won context from this session) yourself. If this session's own note happens to exist —
hand-authored, or from a wiki that predates the retirement — try
`lore workflow seed-lift <path-to-that-note> --wiki-root <wiki-root>` first: on success (exit 0)
use its `origin` and `findings` JSON fields verbatim for those two sections, and add its
`source_note` under **Pointers** as an explicit reference, so a cold `/lore-workflow:orient` on
the seed can pull the note back by path via `lore_read` — `lore_context_pack` no longer
surfaces notes.

Draft each body with the template below. Lead with intent; make the **findings** section rich —
that is the handover value. Pointers are starting points, not gospel (they may go stale).

### 4. Publish
File each seed through [`file-issue`](../file-issue/SKILL.md) in caller-template mode, handing
it the seed template below — that skill keeps the structure exactly and applies the
writing rules inside it. Label each seed `epic-seed` (create the label if missing), link the
originating epic and merged PRs, and do not modify the finished epic.

Finish by printing each seed reference and the command for the fresh session:
`/lore-workflow:orient <owner/repo#seed>`.

## Seed template

<seed-template>
## Intent
What we want to do next, as the seed for discussion — in domain language. Not a solution.

## Origin
Where this came from: the finished epic (`owner/repo#n`), merged PRs/commits, and why it
surfaced. Enough provenance for a cold session.

## Findings from this session
The hard-won context a fresh session would otherwise rediscover: constraints, decisions already
made, dead ends, gotchas, what turned out true or false. This is the point of the seed.

## Open questions
What `/lore-workflow:orient` and the grilling should dig into before forming the epic.

## Pointers (starting points, may be stale)
Relevant modules/files, docs/ADR/CONTEXT, related issues, repos likely involved.

## Out of scope / deferred
What this seed deliberately excludes.

## Next step
A fresh session starts here: `/lore-workflow:orient owner/repo#<this-seed>`.
</seed-template>
