# ADR 0010: A read surface without a producer is a defect; the board carries the orchestrator's narrative

- Status: Accepted
- Date: 2026-08-06
- Context: epic [#375](https://github.com/buchbend/lore/issues/375),
  PRD [0012](../prd/0012-retire-producerless-surfaces.md), pull request
  [#385](https://github.com/buchbend/lore/pull/385); supersedes ADR 0002

## Context

Three releases each deleted a writer and left its readers standing.
Issue #359 removed `lore resume`. Issue #361 removed the compose
pipeline. Epic #131 severed an earlier surface called "surfaces". None
of the three removals swept the readers in the same change.

An audit against `origin/main` at `772e019` found the result: `lore
status` printed four capture rows and a flushes panel fed by nothing,
two alerts fired on every healthy system, and one alert named a state
no code could leave. `lore trace` carried `dead` and `last` selectors
into a flush-lifecycle record nothing wrote any more. The pattern
repeated inside `lore-workflow`: ADR 0002 split orchestrate-epic's
supervision state across the board (per-feature ledger, on the epic
issue) and the epic note (working narrative, on the orchestrator's own
session note). Issue #361 deleted the session-note pipeline the epic
note depended on, so ADR 0002's second store lost its writer along with
every other reader this epic corrects.

A removal reviewed only its own writer, never the readers left pointing
at nothing. That is the gap this ADR closes.

## Decision

1. **A read surface without a live producer is a defect.** A removal
   that deletes a writer deletes or rewires every reader in the same
   change. `lore status`'s rows, `lore trace`'s selectors, a drain event
   kind, and a spine `ErrorCode` value each name their producer; a name
   with no producer is a bug, not a placeholder for one. Issue #377
   applied this rule to the readers named above and added a guard test,
   `tests/test_producerless_surfaces_gone.py`, that fails the build on
   the next producerless surface.
2. **The board carries the orchestrator's supervision narrative. The
   epic note is gone.** `orchestrate-epic` writes tier rationale,
   dispatch and crosscheck reasoning, and escalations into a `## Notes`
   section below the board's per-feature table, in the same GitHub
   comment it already edits in place. `lore workflow parse-board`
   reads the table structurally and ignores the notes section, so a
   board with notes parses identically to one without.

## Consequences / Trade-offs

- **Positive.** Every row `lore status` prints and every selector `lore
  trace` accepts now names a producer a reader can find in the running
  code. A resumed orchestrator reads its prior narrative from the one
  comment it already fetches, with no second store to reconcile and no
  session-note pipeline required to carry it.
- **Positive.** The guard test makes the rule enforceable, not just
  documented — a future removal that forgets a reader fails CI instead
  of shipping a dashboard that lies.
- **Negative.** The board comment is heavier: a supervision run now
  carries prose as well as the table in one place, and a very long run
  risks the comment growing large. Accepted — a second, harder-to-parse
  store was the alternative, and the board was already the durable,
  human-visible artifact.
- **Negative.** `trace_id` correlation (the field, `lore trace <trace-id>`,
  a note's `linkage.trace_id`) has no current minter — its one caller
  was the compose pipeline's flush spawn, retired with it. The
  mechanism stays in the tree for a future producer; documented as
  dormant in `docs/architecture/observability.md`, not deleted, because
  removing it costs a rewrite for no present defect (nothing reads it
  and finds a lie — it simply finds nothing).

## Alternatives considered

- **Leave the guard test out and rely on code review to catch a
  producerless reader.** Rejected: this is exactly what three
  successive removals missed. A rule stated but not enforced repeats.
- **Keep the epic note and delete only the compose pipeline's other
  readers.** Rejected: the epic note's own writer (a composed session
  note) was the pipeline issue #361 removed. Keeping the note as a
  concept while its writer is gone recreates the same defect this ADR
  names.
- **Move the working narrative into a new dedicated store instead of
  the board.** Rejected: the board is already fetched, already edited
  in place every run, and already the durable per-feature record;
  a second store repeats the two-store drift ADR 0002 accepted as a
  trade-off, for no gain now that one store can hold both.

## Status

Accepted. Supersedes ADR 0002 (orchestrate-epic supervision-state
split) — the epic note it specified is gone.
