# ADR 0010: A read surface without a producer is a defect; the board carries the orchestrator's narrative

- Status: Accepted
- Date: 2026-08-06
- Context: epic [#375](https://github.com/buchbend/lore/issues/375),
  PRD [0012](../prd/0012-retire-producerless-surfaces.md), pull request
  [#385](https://github.com/buchbend/lore/pull/385); supersedes ADR 0002

## Context

Three releases each deleted a writer and left its readers standing. Each
removal reviewed only its own writer, never the readers left pointing at
nothing. Issue #359 removed `lore resume`. Issue #361 removed the compose
pipeline. Epic #131 severed an earlier surface called "surfaces". An audit
against `origin/main` at `772e019` found the damage: `lore status` printed
five capture rows and a flushes panel fed by nothing. Two alerts fired on
every healthy system, and one alert named a state no code could leave.
`lore trace` carried `dead` and `last` selectors into a flush-lifecycle
record nothing wrote any more. The pattern repeated inside `lore-workflow`.
ADR 0002 put the orchestrator's working narrative on its own session note.
Issue #361 deleted the session-note pipeline that note depended on. ADR
0002's second store lost its writer along with every other reader named
above.

## Decision

1. **A read surface without a live producer is a defect.** A removal that
   deletes a writer deletes or rewires every reader in the same change.
   `lore status`'s rows, `lore trace`'s selectors, a drain event kind, and
   a spine `ErrorCode` value each name their producer. A name with no
   producer is a bug, not a placeholder for one. Issue #377 applied the
   rule to the readers named above. It added a guard test,
   `tests/test_producerless_surfaces_gone.py`, that fails the build on the
   next producerless surface.
2. **The board carries the orchestrator's supervision narrative. The epic
   note is gone.** `orchestrate-epic` writes tier rationale, dispatch and
   crosscheck reasoning, and escalations into a `## Notes` section below
   the board's per-feature table. Both sections live in the same GitHub
   comment the orchestrator already edits in place. `lore workflow
   parse-board` reads the table structurally and ignores the notes
   section, so a board with notes parses identically to one without.

## Consequences / Trade-offs

- **Positive.** Every row `lore status` prints and every selector `lore
  trace` accepts now names a producer a reader can find in the running
  code. A resumed orchestrator reads its prior narrative from the one
  comment it already fetches. No second store needs reconciling, and no
  session-note pipeline needs to carry it.
- **Positive.** The guard test makes the rule enforceable, not just
  documented. A future removal that forgets a reader fails CI instead of
  shipping a dashboard that lies.
- **Negative.** The board comment carries prose as well as the table now.
  A very long supervision run risks the comment growing large. Accepted —
  a second, harder-to-parse store was the alternative, and the board was
  already the durable, human-visible artifact.
- **Negative.** `trace_id` correlation (the field, `lore trace <trace-id>`,
  a note's `linkage.trace_id`) has no current minter. Its one caller, the
  compose pipeline's flush spawn, retired with the rest of the pipeline.
  The mechanism stays in the tree for a future producer, documented as
  dormant in `docs/architecture/observability.md`. Deleting the mechanism
  would cost a rewrite for no present defect — nothing reads a lie there,
  only an absence.

## Alternatives considered

- **Leave the guard test out and rely on code review to catch a
  producerless reader.** Rejected: code review is exactly what three
  successive removals already passed through. A rule stated but not
  enforced repeats.
- **Keep the epic note and delete only the compose pipeline's other
  readers.** Rejected: the epic note's own writer, a composed session
  note, was the pipeline issue #361 removed. Keeping the note as a
  concept while its writer is gone recreates the same producerless-reader
  defect.
- **Move the working narrative into a new dedicated store instead of the
  board.** Rejected: the board is already fetched every run and already
  edited in place. A second store repeats the two-store drift ADR 0002
  accepted as a trade-off, for no gain once one store can hold both.

## Status

Accepted. Supersedes ADR 0002 (orchestrate-epic supervision-state split)
— the epic note it specified is gone.
