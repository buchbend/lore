---
name: lore-workflow:quick-feedback-loop
description: Hone a feature with the human in fast rounds — they send feedback or screenshots, the agent makes the change on the spot, tests it, commits it and reports what is verified. Skips the PRD and epic weight but keeps the trail - a test per behaviour, docs in the same round, an ADR ledger settled at the end. Use when the user iterates on a working feature ("quick hotfix", "direct edit", "inline", screenshot feedback) and wants turnaround, not a plan.
---

# Quick Feedback Loop

A track for honing a feature that already works, with the human present and
reacting to each result. The epic chain (`to-epic` → `orchestrate-epic`) is far
too heavy for a round of "move this right, make that subtler". Dropping all
discipline is the other trap: fast rounds lose tests, docs and decisions
unless the loop carries them. This skill keeps the **trail** at the weight of
one round.

It **inherits the workflow invariants**: test-first for behaviour, **ruff**
clean, **never commit on red**. It drops the PRD, the epic, the teammates and
the per-feature review. Where the commits land (a feature branch, or local
`main`) is the user's call; ask once if unclear, then keep it.

## When to use

- The user reacts to a running feature: screenshots, "quick hotfix",
  "do it inline", "direct edit".
- Each ask is small and its intent is visible. If an ask grows into several
  features or needs a design, say so and offer `to-epic` or a designer subagent.

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
5. **Docs in the same round.** A change a user sees updates the page that
   describes it (tutorial, how-to, reference, the PRD section if one exists).
   A removed behaviour removes its sentence.
6. **Note ADR candidates.** A decision that may be hard to reverse, surprising
   or a real trade-off goes on the ledger (one line), not into an ADR yet.
7. **Report.** What changed, what the tests verify, what is visual only and
   unseen, the defaults picked. Ask for the next screenshot. Keep it short.

## Traps seen in practice

- A value read back right after writing it through the router (a query
  parameter) is still the old value; keep the new value in a local.
- A leave transition removes its element a frame later; a test that asserts
  absence waits or checks the state instead.
- A test stubbing globals (`matchMedia`) restores them even on failure, or
  every later test in the file fails with it.
- A sandbox without a browser cannot see the page: say that the look is
  unverified rather than implying it.

## Closing the loop

When the user ends the loop, or before a merge or a push:

1. Run the full suites once (backend and frontend), and ruff.
2. Run one review pass with `code-review` at the **mid** tier over the
   cumulative diff of the loop; fix confirmed findings in one more round.
3. Settle the ADR ledger: apply the three criteria of
   [`domain-modeling`](../domain-modeling/ADR-FORMAT.md) to each line. All
   three hold: draft `docs/adr/NNNN-kebab.md`. Otherwise drop the line.
4. Sweep the docs once with `document-epic`'s Diátaxis classification over the
   cumulative diff, for pages the rounds missed.
5. Report the commits, the ADRs drafted, and anything left for a follow-up
   issue ([`file-issue`](../file-issue/SKILL.md)).

## Relationship to the other tracks

- `implement-issue`: one written issue to one PR. This loop has no issue; the
  feedback is the spec.
- `quick-orchestrate` and `orchestrate-epic`: shaped work with a roadmap. A loop
  often follows them, to hone what an epic delivered.
