# Resume a broken epic

**Goal:** pick up an `orchestrate-epic` run that was interrupted — a crash, a
closed laptop, a killed session — without redoing merged work or colliding
with half-merged state.

You do not need a special command. **Resume is the front door:** re-running
`/lore-workflow:orchestrate-epic` on the same epic issue *is* the resume
path. The skill reaches the resume step first — before it validates the
roadmap, before it creates the epic branch, before it opens a status
comment — precisely so a second run heals rather than duplicates.

## Before you start

- The epic issue number or URL from the interrupted run.
- `gh auth status` green (the resume logic reads the epic's comments and
  branch state through the GitHub API).

## Steps

1. **Re-run `/lore-workflow:orchestrate-epic` on the same epic issue.** Point
   a fresh session at the epic and run it exactly as you did the first time.
2. **It finds the prior run's supervision trail.** It lists the epic issue's
   comments and looks for the status comment carrying its per-feature state
   table from the earlier run. That table is written in a machine-readable
   format (a marked table with fixed columns), so the resume reads it
   deterministically with `lore workflow parse-board` rather than
   re-interpreting it by eye — a malformed board is reported as an error,
   never silently misread.
3. **It reconciles against reality.** It compares that table's
   merged / queued / blocked rows against the epic branch's actual state:
   - a feature already merged into `epic/<issue>` is **done** — it is
     skipped, never redispatched;
   - a feature still queued or blocked is **redispatched** from where it
     left off;
   - the `epic/<issue>` branch, if it already exists, is **reused, never
     re-cut**, so the resumed run never collides with half-merged state.
4. **It continues in the same status comment.** It edits that one existing
   comment in place for every further update; a resumed run never opens a
   second status comment.

The board comment carries only the durable per-feature state. The
orchestrator's own working context — tier decisions, crosscheck verdicts,
in-flight markers — rides a single composed **epic note** keyed to the epic,
which the resumed run rehydrates through `lore_context_pack`. So its
reasoning survives the interruption too, not just the feature checklist.

## If no prior run is found

If there is no earlier status comment on the epic, the skill treats this as
a **fresh run**: it proceeds to the roadmap-validator gate and creates the
status comment as normal. So running it on an epic that never started is
safe — it just starts.

## Done when

The run reaches the same completion state a first-pass run would: the epic
pull request merged, every sub-issue closed, the status comment finalized
with no stale queued or running rows.
