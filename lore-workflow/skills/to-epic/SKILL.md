---
name: lore-workflow:to-epic
description: Turn the current conversation or a plan/PRD into a PRD file under docs/prd/ plus
  an epic tracker issue with one sub-issue per feature, structured so /lore-workflow:orchestrate-epic can
  implement it autonomously. Use when the user wants an epic/tracker issue, or to turn a
  plan/PRD into an orchestratable epic.
---

# To Epic

Produce two coupled artifacts that `/lore-workflow:orchestrate-epic` consumes:

1. **The PRD as a file** at `docs/prd/NNNN-kebab.md` (MyST Markdown) — the **source of
   truth**, versioned in the repo, reviewable in PRs, and rendered in the docs site. It is
   auto-wired into `docs/prd/index.md`'s toctree.
2. **The epic tracker issue** — a GitHub issue that **links** the PRD (it does **not embed**
   it), and carries the **roadmap table** (the canonical dependency DAG) plus one sub-issue
   per feature (one feature = one teammate = one branch = one PR).

This skill is the human checkpoint — `/lore-workflow:orchestrate-epic` runs hands-off afterward, so get the
breakdown right here.

Tracker: the project's GitHub issues, via `gh` (use `--json` for reads — see the repo's own
conventions doc, if any). This set is self-contained: it defines its own epic/sub-issue
conventions below and does not rely on an external label vocabulary.

The PRD-in-docs / gh-epic-as-tracker model is implemented by this skill. The deterministic
file mechanics — create the PRD at `docs/prd/NNNN-kebab.md` with front-matter that links the
epic and lists the involved repos, and wire it idempotently into `docs/prd/index.md`'s toctree
— live in `lore`'s `lore workflow create-prd` subcommand, so they are reproducible and tested
independently of this skill:

```
lore workflow create-prd --slug <kebab> --title "<Title>" --epic-url <url> \
    --repo owner/repo [--repo owner/repo-b ...] [--target <repo-root>]
```

It prints the PRD path and wires the toctree; there is no Python helper bundled with this
skill any more.

## Process

### 1. Gather context
Work from the conversation. If the user passes a plan, PRD, or issue reference — including
an epic-seed issue produced by `/lore-workflow:seed-epic` (labeled `epic-seed`) — fetch and read it
(`gh ... --json`; see the repo's own conventions doc, if any).

### 2. Explore the repo(s)
Understand the current state. Identify **every target repo** — an epic may be cross-repo.
Use each project's domain language — its CONTEXT.md / glossary if present — and respect its
ADRs (`docs/adr`).

### 3. Synthesize the PRD
Condense a PRD — do NOT interview, synthesize what you already know: Problem, Solution,
Implementation decisions, Testing decisions, Out of scope. Prefer deep modules testable in
isolation. This PRD is the **source of truth** and becomes the **file** at
`docs/prd/NNNN-kebab.md`, **not** the epic body. The epic body links it and carries only the
one-paragraph summary + roadmap (see Publish).

### 4. Draft the roadmap (vertical slices)
Break the work into **tracer-bullet** features — each a thin slice cutting end-to-end through
all layers, sized as one teammate / one branch / one PR. Prefer many thin slices over few
thick ones. For each feature capture: title, target repo, type (AFK / HITL — prefer AFK),
blocked-by, and acceptance criteria as checkboxes. HITL slices need a human decision or
design review; `/lore-workflow:orchestrate-epic` escalates rather than auto-implements them, so resolve
what you can into AFK during planning.

### 5. Quiz the user (the checkpoint)
Present the breakdown as a numbered list (Title · Repo · Type · Blocked by · criteria) **and
the parallel batches it implies** (topological grouping of the DAG). Ask: right granularity?
dependencies correct? repos correct? AFK/HITL correct? merge or split any slice? Iterate to
approval — `/lore-workflow:orchestrate-epic` will not ask again.

### 6. Publish
Order matters so every cross-reference resolves:

1. **Write the PRD file.** Call `lore workflow create-prd` (see above) to write
   `docs/prd/NNNN-kebab.md` and wire it into `docs/prd/index.md`'s toctree. Front-matter
   carries `epic:` (filled with the epic URL once it exists — step 4 closes this loop) and
   `repos:` (every involved repo). Fill the PRD body sections with the synthesized PRD. Commit
   it to the repo on the branch the docs PR will carry.
2. **Create sub-issues** in dependency order so refs resolve, each in its target repo using the
   sub-issue template (optionally labeled `epic:<n>` for grouping). Each sub-issue **links back
   to the epic**.
3. **Create the epic tracker issue**, labeled `epic` (create the label if missing). The body
   **links the PRD file** and carries a one-paragraph summary + the roadmap table + a task list
   of the sub-issues — **no PRD content is duplicated in the epic body** (link only).
4. **Close the cross-reference loop.** Set the PRD front-matter `epic:` to the epic issue URL,
   and (if an ADR records this decision) cross-link PRD ↔ ADR. The result is **bidirectional
   cross-references** PRD ↔ epic ↔ ADR ↔ sub-issue: a PRD links its epic (and ADR); the epic
   links the PRD and its sub-issues; each sub-issue links back to the epic.
5. **Close the consumed seed.** If this epic was formed from an epic-seed issue (labeled
   `epic-seed`, produced by `/lore-workflow:seed-epic`), close that seed issue with a comment linking the
   newly formed epic (`gh issue close <seed> --comment "Formed into epic <owner/repo#epic>"`).
   Skip this step when the epic was not formed from a seed.

**Gate publishing on the roadmap validator.** The roadmap table is the dependency DAG
`/lore-workflow:orchestrate-epic` consumes, so it must be well-formed before the epic goes live. Run the
deterministic, dependency-free `lore workflow validate-roadmap` on the composed epic body —
e.g. `gh issue view <epic> --json body -q .body | lore workflow validate-roadmap -`, or point
it at the drafted body file (`lore workflow validate-roadmap <path>`). It checks
the required columns (`# | Feature | Issue | Repo | Type | Blocked by`), fully-qualified
`owner/repo#n` Issue refs, blocked-by edges that resolve to rows in the table, and an acyclic
dependency DAG. **Publish only a roadmap it accepts:** a table it rejects is malformed and
would break the orchestrator, so fix the columns, refs, or edges it reports and re-run until
it passes.

Use fully-qualified refs (`owner/repo#n`) for every cross-repo link. Do not modify unrelated
parent issues.

Finish by printing the PRD file path, the epic reference, and the exact next command:
`/lore-workflow:orchestrate-epic <owner/repo#n>`.

## PRD file template

The PRD is the **source of truth**, written to `docs/prd/NNNN-kebab.md` by
`lore workflow create-prd` (front-matter + skeleton) and filled with the synthesized PRD.
Front-matter links the epic and
lists involved repos; the body carries the full spec that the epic body only links to.

<prd-file-template>
---
title: <Title>
status: draft
epic: https://github.com/owner/repo/issues/<epic>
repos:
  - owner/repo
  - owner/repo-b
---

# PRD NNNN: <Title>

> Source of truth for this epic. Tracker: [epic issue](https://github.com/owner/repo/issues/<epic>).
> The epic links here; this file is not embedded in the issue body.

## Problem
The problem, from the user's perspective.

## Solution
The solution, from the user's perspective.

## Implementation decisions
Modules to build/modify and their interfaces, schema/API contracts, architectural decisions.
No file paths or code snippets (exception: a decision-encoding snippet from a prototype —
state machine, schema, type shape — trimmed to the decision-rich parts).

## Testing decisions
What makes a good test here (external behavior, not implementation detail), which modules are
tested, and prior art in the codebase.

## Out of scope
What this epic deliberately does not cover.
</prd-file-template>

## Epic body template

The epic is a **tracker, not a spec**: it **links** the PRD (no PRD content duplicated here)
and carries the roadmap DAG. The **roadmap table stays in the epic body** — it is what
`/lore-workflow:orchestrate-epic` reads.

<epic-body-template>
## Summary
One paragraph: problem → solution. Full spec lives in the PRD.

**PRD:** [`docs/prd/NNNN-kebab.md`](../blob/<branch>/docs/prd/NNNN-kebab.md) — the source of truth.

## Roadmap
Canonical DAG for `/lore-workflow:orchestrate-epic` — one row per feature/sub-issue. Type is AFK (runs
autonomously, no human input) or HITL (human-in-the-loop: needs a human decision); the table
below keeps the literal token, which is what the roadmap validator and `/lore-workflow:orchestrate-epic` read.

| # | Feature | Issue | Repo | Type | Blocked by |
|---|---------|-------|------|------|------------|
| 1 | <slice title> | owner/repo#12 | repo-a | AFK | — |
| 2 | <slice title> | owner/repo#13 | repo-a | AFK | #12 |
| 3 | <slice title> | owner/other#7 | repo-b | HITL | #12 |

- [ ] owner/repo#12 — Feature 1
- [ ] owner/repo#13 — Feature 2
- [ ] owner/other#7 — Feature 3
</epic-body-template>

## Sub-issue template

<sub-issue-template>
## Epic
owner/repo#<epic>

## Repo
The repo this slice is implemented in.

## What to build
End-to-end behavior of this vertical slice — not layer-by-layer implementation. No file paths
or code snippets (same prototype exception as above).

## Acceptance criteria
- [ ] Criterion 1
- [ ] Criterion 2

## Blocked by
owner/repo#<n>, or "None — can start immediately".

## Type
AFK (runs autonomously, no human input) or HITL (human-in-the-loop: needs a human decision).

## Pointers (starting points, may be stale)
Non-authoritative starting points so a teammate reuses the discovery already done
during shaping instead of re-exploring the repo from scratch. Everything here may
have gone stale since the epic was formed — verify before trusting, and widen from
here rather than treat it as the whole picture. The PRD stays path-free; these
pointers live only in this disposable sub-issue (closed on merge), where staleness
is a non-issue.
- **Dev notes** — modules/files to start from and interfaces to build against.
- **Architecture excerpts** — the conventions and shared touchpoints this slice sits within.
- **Test criteria** — tests that are prior art for what a good test looks like here.
</sub-issue-template>
