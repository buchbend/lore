---
title: "Larger epics and code mode"
status: draft
epic: https://github.com/buchbend/lore/issues/644
repos:
  - buchbend/lore
---

# PRD 0024: Larger epics and code mode

> Source of truth for this epic. Tracker: [epic issue](https://github.com/buchbend/lore/issues/644).
> The epic links here; this file is not embedded in the issue body.
> Decision: ADR [0023](../adr/0023-epics-split-on-release-order-not-topic.md) (epics split on
> release order, not topic).

## Problem

### Larger epics

- `to-epic` treats more than 6 slices as two epics, and a change of topic leads to a second epic.
  Each epic costs one cycle of orient, grilling, `to-epic` and a PRD. The planning cycles now
  cost more than the build of a well-cut slice.
- A slice holds one change. Work with an order inside one feature becomes several rows, and
  each row costs a teammate spawn, a review and a merge.
- Every epic gets the same review: correctness per row. An epic that touches auth or a user
  interface gets no security or user-interface review, and an epic without them gets no
  saving.

### Code mode

- The orient homework takes 5 to 8 tool calls: the context pack, the repo docs list and fetch, a
  search, the codemap and the glossary. Each result enters the context unfiltered.
- `lore_context_pack` without a focus issue returns every ADR and every PRD as `ranked`. One
  session on 2026-11-17 got 43 entries, unranked.
- The command line has no `context-pack`, `repo-docs` or `drill` command. An agent cannot chain
  these calls in a script.
- After each merge, the epic lead runs 5 to 6 commands by hand: tick the box, close the
  sub-issue, set the board row, add the candidates and the resume line.

## Solution

### Larger epics

- `to-epic` splits work into two epics only for the three reasons that ADR 0023 names. The soft
  limit is about 12 rows. One PRD holds one `##` section per topic.
- A slice can hold **substeps**, as a numbered checklist in the sub-issue. One teammate builds
  them in order, with one commit per substep.
- `to-epic` proposes a **review plan** at its checkpoint. The user approves or changes the plan.
  Correctness runs per row. Each other lens runs once at the end of the epic, over the rows
  tagged with it.

### Code mode

- An agent writes a short Python script against a `lore` module, runs it with `uv run python -`,
  and reads only the filtered result. The Bash sandbox of the host runs the script.
- `lore brief --json "<intent>"` runs the orient homework in one call. The agent stays free to
  explore further with its own scripts.
- The context pack ranks its docs against an intent and caps the list.
- `lore workflow after-merge` does the epic lead's work after each merge in one call.

## Implementation decisions

### Larger epics

- The build-order table accepts an optional 7th column, `Lenses`, after `Blocked by`. The
  validator accepts the table with and without the column. A lens is one lowercase word, for
  example `security` or `ui`. The table does not list correctness.
- The epic body holds the review plan under `## Review plan`: each lens and the reason for it.
- The sub-issue template gets an optional `## Substeps` section: a numbered checklist. A slice
  without substeps leaves the section out.
- The teammate brief asks for one commit per substep, with the substep number in the commit
  subject, and a ticked box per landed commit.
- The epic tail runs one reviewer per lens, in parallel, over the diff ranges of the tagged
  rows. A finding goes back as a fix round on the epic branch before the epic PR opens.
- The build skill keeps its byte budgets: 6000 bytes for `SKILL.md`, 10000 bytes for
  `SKILL.md` plus one mode file.

### Code mode

- The context pack takes an optional `intent` string. With an intent, the pack ranks the ADRs
  and PRDs by a lexical match of the intent against title and body, and returns the top 8. With
  neither intent nor focus issue, the pack returns the current ADRs only. The MCP tool and the
  new command take the same parameter.
- A module `lore.api` re-exports the functions behind the MCP tools: search, read, drill, repo
  docs list and fetch, context pack and codemap. Each function returns plain data (dicts and
  lists) and prints nothing. The module docstring lists the functions with one line each, so
  `python -c "import lore.api; help(lore.api)"` is the whole reference.
- `lore brief --json "<intent>"` returns one object: the ranked pack, the first section of each
  top doc, a codemap excerpt of about 1000 tokens, and the glossary entries of the domain words
  in the intent. The same input gives the same output.
- `orient` calls `lore brief` first, then the agent drills with `lore.api` scripts where the
  brief is thin.
- `lore workflow after-merge --board - --issue <n> --sha <sha>` reads the board comment from
  stdin, ticks the build-order box, closes the sub-issue with the merge comment, sets the row
  to `merged`, appends the resume line, and prints the updated board. `epic.md` calls it in
  place of the separate steps.
- The MCP tools stay. A host without a shell keeps them.

## Testing decisions

- The validator: a table with `Lenses` passes, a table without it passes, a table with an
  unknown 8th column fails.
- Skill text: the structural tests pin the split reasons, the substep rule, the review-plan
  rule and the byte budgets.
- The context pack: with an intent, the top entry is the doc that names the intent words; with
  neither intent nor focus, the pack holds no shipped PRD.
- `lore.api`: each exported name resolves and returns plain data on an empty repo.
- `lore brief`: the same intent on the same tree gives the same output.
- `after-merge`: given a board comment and an epic body, the command produces the expected
  board; the GitHub calls are faked, as in the existing board tests.
- After the merge: one lab entry through `/lore:experiment` compares the orient and epic-lead
  tokens of two runs before and after, from the existing per-stage token trace.

## Out of scope

- An MCP execute tool or any new code runner. The host's sandbox runs the scripts.
- One run that builds several epics in a row.
- A command-line copy of every MCP tool.
- Fewer filed records per row: each row keeps its sub-issue, branch, review and ledger lines.
