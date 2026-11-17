# ADR 0023: Epics split on release order, not on topic

- **Status:** Proposed
- **Date:** 2026-11-17
- **Deciders:** Christof Buchbender
- **Relates to:** PR [#643](https://github.com/buchbend/lore/pull/643) (fix small follow-ups in
  the PR, ask questions without stopping); the `to-epic` and `build` skills
- **See also:** ADR [0014](0014-one-home-per-fact.md) (one home per fact)

## Holds

- **Default:** `to-epic` splits work into two epics only for one of three reasons:
  - one part has to wait until another part is deployed or published;
  - a decision by the user gates most of the rows;
  - the repos share no release.
  A change of topic is no reason to split.
- **Default:** an epic with several topics holds one PRD, with one `##` section per topic.
- **Default:** each row of the build order keeps its own sub-issue, branch, review and ledger
  lines. A larger epic files the same record as two smaller ones.
- **Default:** a slice can hold substeps. One teammate builds them in order on one branch, with
  one commit per substep, and one review covers the slice.
- **Default:** `to-epic` proposes a review plan for each epic, and the user approves or changes
  the plan at the `to-epic` checkpoint. The plan names the review lenses, for example
  correctness, security or user interface. Correctness is in every plan. The lenses of one
  review run in parallel.
- **Default:** one epic PR per epic, so CI runs once. The whole-epic review goes through the
  epic one topic at a time.
- **Incidental:** the soft limit is about 12 rows per epic. It was 6.

## Revisit if

- Review quality drops on epics with several topics: confirmed findings after the merge rise
  against the single-topic epics before this ADR.
- A teammate on a slice with substeps runs out of context before the slice is done.
- The epic PR gets too large for CI or for the person who gives the merge approval.

## Alternatives considered

- **Keep one topic per epic, and chain the epics in one run.** The `epic-lead` agent and the
  super-orchestrate path can do this. Rejected: each epic still costs one orient, grilling,
  `to-epic` and PRD cycle, and the issues get more noise.
- **Larger epics with fewer filed records.** For example one sub-issue per topic, not per row.
  Rejected: the record per row is how a later session finds why a change was made. The speed
  has to come from fewer planning cycles, not from less filing.
- **The same reviewer set for every epic.** Rejected: a security or user-interface review costs
  time on an epic that touches neither. The user knows best which lenses an epic needs.

## Background

Agents now build a well-cut slice without help, so the planning cycles around an epic cost
more than the build. The question rule of PR #643 means that an open decision no longer stops a
run. The question rule takes away one more reason for small epics.

## Amendments

None.
