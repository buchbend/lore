# ADR 0005: Docs land with the epic, not after it

- Status: Accepted
- Date: 2026-08-03
- Context: workflow-lightening review session, 2026-08-03 (with the wider
  lightening pass: DAG-derived effort band, linear-chain slice collapse,
  batched crosscheck)

## Context

`document-epic` ran as `orchestrate-epic`'s *final* stage: after the epic PR
merged, it opened a separate docs PR that auto-merged on green, with human
review post-hoc. That made documentation a structural afterthought — the epic
was "done" before its docs existed, the whole-epic reviewer never saw them,
and every epic produced a second PR trailing the first.

## Decision

`orchestrate-epic` invokes `document-epic` in a **pre-land mode**, after the
last feature merges into `epic/<n>` and before the epic PR opens. In this
mode the documenter commits the Diátaxis doc updates **directly onto the epic
branch** — no separate docs PR. Consequences by construction:

1. The epic PR carries code and docs as one reviewable unit; the whole-epic
   review checks docs-vs-behavior mismatches alongside cross-feature
   consistency.
2. The epic's own CI gates the docs; there is no second merge to track.
3. **Docs never block a green epic.** If the docs stage escalates or cannot
   go green after one fix pass, the orchestrator drops the docs commit, lands
   the epic, and runs standalone `document-epic` post-land — the prior
   behavior, kept as fallback and as the catch-up path for already-merged
   epics.

The hard rule is unchanged: `document-epic` never touches `docs/prd/` or
`docs/adr/` in either mode.

## Consequences / Trade-offs

- **Positive.** Docs are part of the deliverable, reviewed and gated with the
  code they describe; one PR per epic instead of two.
- **Negative.** Landing waits on the docs stage (bounded by the one-fix-pass
  fallback), and a docs defect can now fail epic CI — mitigated by the same
  fallback.

## Alternatives considered

- **Keep the post-land docs PR (prior design).** Rejected: it is the
  afterthought this ADR removes — unreviewed by the epic reviewer, trailing
  the merge.
- **Docs per feature PR (each teammate documents its slice).** Rejected:
  N teammates writing into the same docs tree invites conflicts, and slice-
  local docs miss the cross-feature view; one documenter over the cumulative
  diff is cheaper and more coherent.
