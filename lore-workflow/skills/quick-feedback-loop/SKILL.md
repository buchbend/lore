---
name: lore-workflow:quick-feedback-loop
description: Hone a feature with the human in fast rounds — each round is coded test-first in a worktree and merged straight into the branch the live dev stack runs, so the human sees it and sends the next ask. ADR and glossary candidates collect on a ledger; on "wrap up" the human approves them, they are written, and a docs pass runs. Use for small iterative work ("quick hotfix", "direct edit", screenshot feedback) where grilling, orchestration and a PR per change are too heavy.
---

# Quick Feedback Loop

A track for small, iterative work with the human present and reacting to each
result in the running dev stack. The epic chain, `grilling` and a PR per change
are far too heavy for "move this right, make that subtler". Dropping all
discipline is the other trap: fast rounds lose tests, decisions and vocabulary
unless the loop carries them. This skill keeps **lore's standards for ADRs,
glossary and docs**. It defers the writing of all three to the wrap-up.

It **inherits the workflow invariants**: test-first for behaviour, **ruff**
clean, **never merge on red**. It drops the PRD, the epic, the teammates, the
interview and the per-change PR.

## When to use

- The user reacts to a running feature: screenshots, "quick hotfix",
  "do it inline", "direct edit".
- Each ask is small and its intent is visible. If an ask grows into several
  features or needs a design, say so and offer `brief` or the epic chain.

## Setup (once per loop)

1. **Find the target branch.** The target is the branch the live dev stack
   serves: `develop` or `main`. Read the branch checked out in the primary
   checkout (`git worktree list`, first row). It is `develop` or `main`: that
   is the target. Anything else, or both branches look plausible: **ask**.
2. **Create the worktree.** Branch `loop/<slug>` off the target, in a sibling
   worktree. All edits happen there; the primary checkout only receives merges.
3. **Open the ledger** at `$(git rev-parse --git-dir)/quick-feedback-loop.md`,
   run inside the worktree. Git never tracks that path, so the ledger never
   merges. It survives context compaction and dies with the worktree.
   First line: `base: <sha>`, the target's commit the loop started from.
   Then two sections: `## ADR candidates` and `## Term candidates`.

## One round

1. **Restate the asks.** One line per ask. An ambiguous ask gets a default
   you pick and name in the report; do not stall on it. Ask only when a wrong
   guess would be costly to undo.
2. **Read before editing.** Trace the code each ask touches, including shared
   helpers. A helper that rebuilds a record from known fields drops a new field
   silently; grep its callers.
3. **Test first where there is behaviour.** Use [`tdd`](../tdd/SKILL.md) for
   anything a test can observe: a handler, a state change, an API field, a
   keyboard path, a phone-width branch (stub `matchMedia`). For a CSS rule that
   carries behaviour, such as a media query that turns a panel into an overlay,
   assert the rule in the stylesheet. Pure looks (colour, spacing, motion) get
   no test; list them as "visual only" in the report.
4. **Run the scoped tests, read the result, then commit.** Never chain the
   commit behind the test command; a green exit of a pipe is not a green run.
   One commit per round, a message that names the behaviour.
5. **Merge into the target.** In the primary checkout: `git merge --ff-only
   loop/<slug>`. The primary checkout is dirty, on another branch, or the
   merge is not a fast-forward: stop and ask. Never merge on red. The dev
   stack does not hot-reload the change: restart it the way the repo documents.
6. **Note candidates on the ledger.** One line each, with the round's commit:
   - **ADR candidate**: a decision that may be hard to reverse, surprising
     without context, *and* a real trade-off. Use the three criteria of
     [`domain-modeling`](../domain-modeling/SKILL.md), not every small choice.
   - **Term candidate**: a domain word the user or the code uses that
     `CONTEXT.md` lacks, conflicts with, or uses loosely. Domain words only;
     general programming words stay out (see
     [`CONTEXT-FORMAT.md`](../domain-modeling/CONTEXT-FORMAT.md)).
7. **Report.** What changed, what the tests verify, what is visual only and
   unseen, the defaults picked, new ledger lines. Say it is live in the dev
   stack. Ask for the next round. Keep it short.

## Traps seen in practice

- A value read back right after writing it through the router (a query
  parameter) is still the old value; keep the new value in a local.
- A leave transition removes its element a frame later; a test that asserts
  absence waits or checks the state instead.
- A test stubbing globals (`matchMedia`) restores them even on failure, or
  every later test in the file fails with it.
- A sandbox without a browser cannot see the page: say that the look is
  unverified rather than implying it.

## Wrap up

When the user says "wrap up" (or ends the loop):

1. **Full suites and ruff**, once, on the worktree branch. Fix red in one more
   round before anything else.
2. **Review.** One `code-review` pass at the **mid** tier over the cumulative
   diff `<base>..loop/<slug>`, with `base` from the ledger. Fix confirmed
   findings in one more round.
3. **Present the ledger.** One message, two lists. Per ADR candidate: the
   decision, the alternatives, and your verdict on each of the three criteria.
   Per term candidate: a proposed definition and `_Avoid_` words. The user
   approves, edits or drops each line in one reply.
4. **Write what was approved.** Run `lore style show writing-rules` first;
   every ADR and definition obeys those rules.
   - ADRs: `docs/adr/NNNN-kebab.md` in the format of
     [`ADR-FORMAT.md`](../domain-modeling/ADR-FORMAT.md).
   - Terms: `CONTEXT.md` (or the context named in `CONTEXT-MAP.md`) in the
     format of [`CONTEXT-FORMAT.md`](../domain-modeling/CONTEXT-FORMAT.md).
   Never write a line the user did not approve.
5. **Docs pass.** Classify the cumulative diff `<base>..loop/<slug>` with
   `document-epic`'s Diátaxis
   method (`lore_workflow.diataxis.classify_changeset`) and update each touched
   quadrant: tutorial, how-to, reference, explanation. Leave `docs/prd/` and
   `docs/adr/` to step 4. A removed behaviour removes its sentence.
6. **Merge and report.** Commit steps 4 and 5 on the worktree branch, test,
   merge into the target as in a round. Report the commits, ADRs and terms
   written, and leftovers for a follow-up issue
   ([`file-issue`](../file-issue/SKILL.md)). **Pushing is the user's call**;
   a protected target takes one PR instead. Remove the worktree and branch
   once the user confirms.

## Relationship to the other tracks

- `implement-issue`: one written issue to one PR. This loop has no issue; the
  feedback is the spec, and merges are local and continuous.
- `brief`: aligns an unwritten task in one exchange. A loop can follow it once
  the shape is clear.
- `quick-orchestrate` and `orchestrate-epic`: shaped work with a roadmap. A loop
  often follows them, to hone what an epic delivered.
