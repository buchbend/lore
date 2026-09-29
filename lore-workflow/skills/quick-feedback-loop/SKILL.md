---
name: lore-workflow:quick-feedback-loop
description: Hone a feature with the human in fast rounds — each round is coded test-first in a worktree, checked by its new tests only, and merged straight into the branch the live dev stack runs; a background subagent runs the full suite and reports breakage. ADR and glossary candidates collect on a ledger; on "wrap up" the human approves them, they are written, a docs pass runs, and an optional architect and web-design pass is offered. Use for small iterative work ("make this button red", "move this plugin there", "change a hotkey", screenshot feedback) where grilling, orchestration and a PR per change are too heavy.
---

# Quick Feedback Loop

A track for small, iterative work with the human watching the running dev
stack. The epic chain, `grilling` and a PR per change are too heavy for it;
dropping all discipline loses tests, decisions and vocabulary. This skill keeps
**lore's standards for ADRs, glossary and docs** and writes all three at wrap-up.

It **inherits the workflow invariants**: test-first for behaviour, **ruff**
clean, **never merge on red**. Red here means the round's own tests; the full
suite runs in the background and never blocks a round. It drops the PRD, the
epic, the teammates, the interview and the per-change PR.

## When to use

- The user reacts to a running feature: screenshots, "quick hotfix",
  "do it inline", "direct edit".
- Each ask is small and its intent is visible: make this button red, move this
  plugin there, change a hotkey.

## When to stop and advise design

Users braindump bigger features into the loop too. Before coding an ask, check
it. Any one of these signs means it needs proper design first:

- It adds a new data model, table, API endpoint or persisted state.
- It spans several features or more than one component boundary.
- It forces a choice between real alternatives the user has not made.
- It touches auth, permissions, secrets or data other users can see.
- You cannot restate it as one testable behaviour.

Then say so in one line, name the sign, and recommend a track: `brief` for a
single change in the user's head, `grilling` then `to-epic` for an unsettled
or multi-feature shape. Offer to note it as a follow-up and carry on with the
small asks. The user may still say "do it anyway"; then slice it into small
rounds.

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
4. **Run only the round's tests, read the result, then commit.** Run the
   tests this round added or changed, by file or node id, plus ruff on the
   touched files. Never the whole suite in the main session. Never chain the
   commit behind the test command; a green exit of a pipe is not a green run.
   One commit per round, a message that names the behaviour.
5. **Merge into the target.** In the primary checkout: `git merge --ff-only
   loop/<slug>`. The primary checkout is dirty, on another branch, or the
   merge is not a fast-forward: stop and ask. Never merge on red. The dev
   stack does not hot-reload the change: restart it the way the repo documents.
6. **Start the background check.** Spawn one background subagent at the
   `cheap` tier (`lore tier resolve cheap`, see
   [`TIER-DELEGATION.md`](../../TIER-DELEGATION.md)). It runs the full suite
   and ruff on the merged commit, read-only, and reports failures with test
   ids, or "green". A new round replaces a check still running. Do not wait
   for it. A failure report makes the fix the first ask of the next round.
7. **Note candidates on the ledger.** One line each, with the round's commit:
   - **ADR candidate**: a decision that may be hard to reverse, surprising
     without context, *and* a real trade-off. Use the three criteria of
     [`domain-modeling`](../domain-modeling/SKILL.md), not every small choice.
   - **Term candidate**: a domain word the user or the code uses that
     `CONTEXT.md` lacks, conflicts with, or uses loosely. Domain words only;
     general programming words stay out (see
     [`CONTEXT-FORMAT.md`](../domain-modeling/CONTEXT-FORMAT.md)).
8. **Report.** What changed, what the tests verify, what is visual only and
   unseen, the defaults picked, new ledger lines, and the last background
   result. Say it is live in the dev stack. Ask for the next round. Keep it
   short.

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

1. **Full suites and ruff**, once, on the worktree branch, in the foreground.
   Fix red in one more round before anything else.
2. **Offer the advisory pass.** Ask the user whether to run it; skip it on a
   no. On a yes, spawn two subagents in parallel at the `strong` tier over the
   cumulative diff `<base>..loop/<slug>`, with `base` from the ledger:
   - **Architect**: security, code layout, duplication, correctness risks.
   - **Web design**: style consistency, accessibility, responsive layout.
     Skip this one when the diff touches no UI.
   Both return a short ranked list of refactor suggestions and change no code.
   Present the lists; the user picks which to do as extra rounds.
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

`implement-issue` takes one written issue to one PR; here the feedback is the
spec and merges are local. A loop often follows `brief`, `quick-orchestrate`
or `orchestrate-epic`, to hone what they delivered.
