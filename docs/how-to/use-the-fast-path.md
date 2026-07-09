# Use the fast path

**Goal:** implement one well-understood GitHub issue directly, keeping the
workflow's discipline without the weight of the full epic chain.

The fast path is `/lore-workflow:implement-issue` — a **second track beside
the epic chain, not a step in it**. It exists to avoid two traps: running
the whole shaping-to-build chain for a change that does not need it, and
bypassing the workflow entirely and dropping the discipline the workflow
exists to keep.

## When this is the right track

Reach for the fast path when the user hands you a **single** GitHub issue
they wrote, and the change is small and clear enough that the epic chain
would be pure overhead. If the work is really several features, or the shape
is still unsettled, it belongs on the [epic chain](run-an-epic.md), not
here.

## Before you start

- A single, clear GitHub issue. A good one makes this track fly — see
  [Write a good fast-path issue](write-a-fast-path-issue.md).
- The repo onboarded, so `CODEMAP.md` is present (`lore codemap` / the
  `SessionStart` hook keeps it fresh — see [Onboard a repo](onboard-a-repo.md)).

## Steps

1. **Point the skill at the issue.** Run `/lore-workflow:implement-issue`
   with the issue.
2. **It reads the issue and the code map.** That is the whole intake — no
   exploration fan-out. `CODEMAP.md` (or the `lore_codemap` MCP tool for a
   bounded query) is a ranked navigation index built for exactly this: find
   the symbol, open the cited file and line.
3. **It clarifies only if needed.** If the issue is ambiguous, it asks you
   **at most three** targeted questions and waits. If the issue is already
   clear, it proceeds with no interview.
4. **It implements with strict test-first development.** One branch,
   `feat/<issue>-slug`, off the detected target branch; a failing test
   first, then the code that passes it; everything in one pull request.
5. **It applies the same gates the epic chain applies.** The
   architecture-decision-record check (an ADR is drafted only if the
   decision is hard to reverse, surprising, and a real trade-off), and the
   Diátaxis docs pass over the change's diff. It never edits `docs/prd/` or
   `docs/adr/` beyond a warranted ADR.
6. **It reviews once and opens the pull request.** One review pass at the
   mid tier by default (lighter than the epic chain's strong-tier
   crosscheck), then it opens the pull request linking the issue.

## What is different from the epic chain

- **One issue, one branch, one pull request.** No fan-out, no multi-PR
  integration, no epic branch.
- **Merging stays with you.** You are present on this track, so the skill
  opens the pull request and stops. It never self-merges. It never merges on
  red.

## Done when

The pull request is open, green, and linked to the issue it closes — ready
for you to merge.
