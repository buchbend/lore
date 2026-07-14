---
name: lore-workflow:consolidate-docs
description: Pass over an existing documentation tree that has grown wild and consolidate
  it — inventory, classify into Diátaxis quadrants, find duplication/contradiction/orphans,
  propose a consolidation plan, and apply it as one docs PR only after the user approves the
  plan. NEVER touches docs/prd or docs/adr. Use when docs have wild growth, overlap, or no
  clear structure. Triggers on "consolidate docs", "docs cleanup", "/lore-workflow:consolidate-docs".
---

# Consolidate Docs

You are the **consolidator**: a documentation tree has grown wild — overlapping pages,
prose that restates the API, orphans nothing links to — and it needs to converge back into
a coherent Diátaxis shape. Unlike `document-epic` (which extends docs after one epic's
delta), this skill sweeps the **whole existing tree** and reduces it.

**Input:** a docs root (a repo's `docs/`, or a path the user names).
**Mode:** plan-first — the consolidation plan is reviewed by the user **before** any file
is moved, merged, or deleted. Deleting human-written docs is not a call this skill makes
alone.

## Hard rule — never edit the canonical record

Same rule as `document-epic`: **NEVER modify anything under `docs/prd/` or `docs/adr/`.**
Read them for context; never write, move, rename, or delete them. `is_excluded(path)` from
`lore_workflow.diataxis` flags these paths — trust it and re-check your final diff.

## Loop

### 1. Inventory

Walk the docs root and build one table: every doc file with its path, title, headings, and
inbound links (grep the tree for references to it — toctrees, relative links, wikilinks).
Note which files no toctree/index reaches (**orphans**) and which links point at files that
no longer exist (**dangles**).

### 2. Classify

Run every path through `classify(path, ...)` / `classify_changeset` from
`lore_workflow.diataxis` to get each doc's quadrant (tutorial / how-to / reference /
explanation) or "skip"/"excluded". Where the directory layout gives no signal, judge the
quadrant from the content: is it teaching (tutorial), a recipe (how-to), describing the API
(reference), or explaining why (explanation)? A doc that mixes quadrants is itself a
finding — Diátaxis pages serve one need each.

### 3. Find the wild growth

For each quadrant, look for:

- **Duplication** — two pages covering the same task/concept; propose merging into the
  better-located one, with the survivor absorbing anything unique from the loser.
- **Contradiction** — pages that disagree with each other or with the implemented behavior;
  verify against the code before proposing which side wins.
- **Prose-restated reference** — hand-written pages paraphrasing the API; propose replacing
  with docstrings + autosummary/toctree wiring, the repo's reference convention.
- **Mixed-quadrant pages** — propose the split (or the dominant-quadrant home).
- **Orphans and dangles** — propose wiring in, merging, or deleting; a dangle is fixed at
  the link, an orphan needs a decision.
- **Stale content** — flag only on **named positive evidence**: a contradiction with the
  code, a reference to a removed feature, a broken link. Age alone never flags a doc.

### 4. Propose the plan

Present one consolidation plan: per action — `merge A into B`, `move`, `split`, `delete`,
`rewire link`, `convert to docstring reference` — with a one-line reason each. Group by
quadrant, deletions listed last and most explicitly. **Wait for the user's approval**; they
may strike individual actions. Do not proceed on silence.

### 5. Apply as one docs PR

On approval, execute the surviving actions on a branch off the repo's integration branch.
Preserve content when merging — the survivor absorbs, the loser is deleted only after its
unique content has a home. Fix every link/toctree the moves break; a consolidation that
leaves new dangles has failed its own step 3. Re-confirm no `docs/prd`/`docs/adr` path is
in the diff. Open one PR summarizing plan → applied actions. **Merging stays with the
user** — unlike `document-epic`, this PR touches human-written pages and does not
auto-merge.

## Relationship to document-epic

`document-epic` is incremental (one epic's delta, autonomous, auto-merge on green);
`consolidate-docs` is a periodic sweep (whole tree, plan-approved, human-merged). Run this
when growth has outpaced structure; run `document-epic` after every epic so it stays rare.
