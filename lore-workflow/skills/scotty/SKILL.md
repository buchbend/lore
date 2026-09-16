---
name: lore-workflow:scotty
description: Remodel an existing epic so /lore-workflow:orchestrate-epic runs it with fewer
  slices — folds linear chains and slices that write the same files into one sub-issue,
  rewrites the roadmap, closes the absorbed sub-issues. Use when an epic has too many rows
  for its real parallelism, or the user says "scotty", "fold the epic", "fewer slices".
---

# Scotty

> "I cannae change the laws of physics, Captain — but I can take out the parts ye dinnae need."

Every roadmap row costs `/lore-workflow:orchestrate-epic` a teammate spawn, a worktree, a
crosscheck, a merge and a rebase of every later sibling. Two rows that write the same file also
buy a rebase conflict. Scotty takes an epic that already exists and folds it down to the fewest
rows that earn their overhead, using the same split rule `/lore-workflow:to-epic` plans by. Scotty
changes nothing about how the epic is implemented; it only changes how the work is cut.

**Input:** an epic tracker issue (number or URL) whose body carries the roadmap table.
**Output:** the same epic, fewer rows, every ref still resolving, `validate-roadmap` green.

## Invariants
- **The roadmap table is the only DAG.** Read it with `lore workflow validate-roadmap --json`,
  never by eye. Refuse a roadmap the validator rejects; fix the epic first.
- **A row on the board that is `running`, `crosscheck` or `merged` never folds.** Fetch the epic's
  board comment (if any) and pipe it to `lore workflow parse-board`; those rows are frozen.
- **A fold reuses the lowest-numbered surviving sub-issue.** The others are absorbed into it and
  closed. No new issue numbers, no dangling refs.
- **Never fold across repos. Never fold a HITL row into an AFK row.** A HITL row isolates a human
  decision; keeping it apart is the point.
- **One human checkpoint, before any write.** Scotty edits GitHub issues, so it presents the fold
  plan and waits for a yes. After the yes it runs to the end without asking again.

## Loop

**Read.** `gh issue view <epic> --json body,comments`. Run the body through
`lore workflow validate-roadmap --json` → `{ok, rows, repos, edges, problems}`. Fetch each
sub-issue body. For every row collect: repo, type, blocked-by, acceptance criteria, and the
**write set** — the files and modules its Pointers (Dev notes) and Required behaviour name. Widen
a thin write set once with `lore codemap` (or `lore_codemap`) against the row's named symbols;
never explore the repo per row.

**Score.** Build the DAG from `edges` and split it into dependency-ordered batches, exactly as the
orchestrator will. Then mark every fold candidate, in this order:
1. **Linear chain.** Row B is blocked only by A, and A is a blocker of nothing but B. Fold B into
   A. Repeat until no chain remains. The orchestrator serialises a chain anyway; the extra rows
   buy only overhead.
2. **Shared write set.** Two rows in the same batch (or adjacent batches) name the same file. Fold
   the later into the earlier. Parallel teammates on one file end in a rebase conflict that
   returns to a teammate; one teammate editing that file once does not.
3. **Small remainder.** After 1 and 2, if the roadmap holds `rows ≤ 3` in one repo, the
   orchestrator drops to compact mode (one teammate, sequential). Any row left that shares a
   batch with nothing gains no parallelism from its own row; fold it into its nearest dependency
   neighbour unless it is HITL or risk isolation is stated in its sub-issue.

Stop folding when every remaining pair of rows either sits in different batches with a real
dependency between them, or runs side by side with disjoint write sets. That is the fewest slices
that earn their split. A fold that would leave one row carrying more than one repo, or more than
one type, is not a fold.

**Checkpoint.** Print two roadmap tables — before and after — and, under each, the batch list the
orchestrator would run. For each fold state the rule that fired (chain / shared file `<path>` /
small remainder) in one line. Print the numbers that change what the user decides:

| | before | after |
|---|---|---|
| rows | n | m |
| batches | n | m |
| shared-file pairs | n | 0 |

Ask once: fold as shown, drop any fold, or stop. Iterate to a yes.

**Apply.** In this order so every ref resolves at every step:
1. **Rewrite each surviving sub-issue** through [`file-issue`](../file-issue/SKILL.md) in
   caller-template mode with the sub-issue template from
   [`to-epic`](../to-epic/SKILL.md). Required behaviour, Acceptance criteria and Pointers are the
   union of the survivor and its absorbed rows; each absorbed row's criteria stay verbatim so the
   teammate's tests still map to them one to one. Blocked-by becomes the union of the absorbed
   rows' blockers minus any issue now inside the fold.
2. **Close each absorbed sub-issue** with
   `gh issue close <n> --comment "Folded into owner/repo#<survivor> by /lore-workflow:scotty"`.
3. **Rewrite the epic body.** Replace the roadmap rows and the task list; renumber `#`; retarget
   every `Blocked by` that named an absorbed issue to its survivor. Write the body to a file, run
   `lore workflow validate-roadmap <path>`, and post only a body it accepts —
   `gh issue edit <epic> --body-file <path>`. Nothing else in the epic body changes.
4. **Leave a trail.** One comment on the epic: the fold map (`#absorbed → #survivor`, rule) and
   the before/after counts. Never edit an orchestrator board comment; if one exists, its
   `queued` rows for absorbed issues are stale and the orchestrator's Resume step re-reads the
   epic body, so note that in the same comment.

**Report.** Rows and batches before → after, the survivor list, and the exact next command:
`/lore-workflow:orchestrate-epic <owner/repo#epic>`.

## Stop conditions
The validator rejects the roadmap; a board shows every row already past `queued`; the fold plan
finds nothing to fold (say so and stop — an epic already cut right is not a failure); or a
sub-issue's write set cannot be told from its text and the codemap widening (ask which file it
owns, then continue).
