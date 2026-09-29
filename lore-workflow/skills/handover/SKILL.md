---
name: lore-workflow:handover
description: Write the handover section for work that stops before its finish point, and turn
  follow-up work into a seed issue a fresh session can orient on cold. Also holds the shape of
  the handover section that build writes at every finish point. Use when a session is too long
  to continue, a build run stops early, or follow-up work surfaced and should go to a new session.
---

# Handover

**Tier note:** this step runs in the main session at frontier-tier. It is not delegated to a
subagent.

Run `lore style show writing-rules` before you write. The handover section and the seed issue
are team-facing text.

## The handover section

One `## Handover` section, in one home:

| Work | Home |
|---|---|
| `build` in `epic` mode | the epic issue body |
| `build` in `loop` mode | the wrap-up PR body |
| `build` in `issue` mode | the PR body |
| work with no PR and no epic | a seed issue (below) |

Shape:

```md
## Handover

- **Shipped:** <what merged, with the merge SHA> | nothing yet
- **Decisions:** <ADR links, one per line> | none
- **Deviations from the PRD:** <what differs and why> | none
- **Follow-ups:** <owner/repo#n, one per line> | none
- **Known limits:** <what does not work yet, where it was observed> | none
```

Work that stops before its finish point adds two lines:

```md
- **State:** <open branches, worktrees and PRs; the ledger path and its last resume line>
- **Next step:** <the exact command a fresh session runs>
```

Link to each home; do not restate an ADR or an issue. A missing fact gets `TODO:` and the
question.

## When work stops early

1. **Resolve what you can.** Run `lore workflow ledger-check` (epic: pipe the board comment into
   `lore workflow ledger-check -`). File each `left` line you can through
   [`file-issue`](../file-issue/SKILL.md) and set it `filed`. Leave ADR and term candidates open
   for the next session; name them under **State**.
2. **Write the handover section** into its home. With no home, write a seed issue.
3. **Leave the resume line.** `lore workflow ledger-add --kind resume --text "<state>" --next
   "<next ask>"` (epic: with `--path -`, pasted into the board's `## Ledger`).
4. Tell the user that `/clear` is safe, and print the next step.

## Seed issue

A seed is a discussion seed, not a spec. It carries the intent and the context that is expensive
to rebuild. The new session forms the work through `orient → grilling → to-epic`. Do not slice or
decide scope here.

1. **Harvest** the follow-up work from this session: new problems, deferred ideas, loose ends,
   traps met. Take it from the context; do not interview.
2. **Group** the threads into seeds, one future epic each. A small, clear follow-up needs no
   seed: file it as an issue. Confirm the grouping with the user.
3. **Write** each seed in the template below. Make **Findings** rich; that is the handover value.
   When a session note for this session exists, try `lore workflow seed-lift <note> --wiki-root
   <wiki-root>` first. On exit 0, use its `origin` and `findings` fields, and add its
   `source_note` under **Pointers**.
4. **Publish** each seed through [`file-issue`](../file-issue/SKILL.md) in caller-template mode.
   Label it `epic-seed` (create the label if missing). Link the originating epic and PRs. Do not
   edit the finished epic.

Print each seed reference and the command for the fresh session:
`/lore-workflow:orient <owner/repo#seed>`.

<seed-template>
## Intent
What we want to do next, as the seed for discussion, in domain language. Not a solution.

## Origin
Where this came from: the epic (`owner/repo#n`), merged PRs and commits, and why it surfaced.

## Findings from this session
The context a fresh session would rediscover: constraints, decisions made, dead ends, traps,
what turned out true or false.

## Open questions
What `orient` and the grilling dig into before the work takes shape.

## Pointers (starting points, may be stale)
Modules and files, docs, ADRs, `CONTEXT.md`, related issues, repos involved.

## Out of scope / deferred
What this seed leaves out.

## Next step
A fresh session starts here: `/lore-workflow:orient owner/repo#<this-seed>`.
</seed-template>
