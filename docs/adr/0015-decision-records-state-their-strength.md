# ADR 0015: Decision records state their strength

- **Status:** Accepted
- **Date:** 2026-09-29
- **Deciders:** Christof Buchbender
- **Relates to:** PRD [0015](../prd/0015-slim-workflow-around-homes-and-holds.md);
  ADR [0006](0006-issue-register-whole-file-override-generation-time-lint.md) (writing rules)

## Holds

- **Invariant** (test: `tests/test_lint_adr.py::test_an_adr_without_holds_fails_and_names_the_file`):
  every ADR from 0014 onward carries a `Holds` section. `Revisit if` and
  `Amendments` stay defaults.
- **Invariant** (test: `tests/test_lint_adr.py::test_an_invariant_naming_a_missing_test_function_fails_and_names_the_line`):
  an invariant line names the test that enforces it, as
  `test: <path>::<name>`. A line without a test is a default.
- **Default:** agents read ADRs and PRDs as context. Only invariants bind.
  An agent that deviates from a default names the deviation and the reason
  in the PR.
- **Default:** a relaxed or sharpened rule becomes a dated amendment. A
  reversal of the whole decision takes a superseding ADR.
- **Default:** a PRD whose epic merged becomes a shipped PRD. Retrieval
  ranks it below ADRs and does not inject it.
- **Incidental:** the reading rule reaches agents through one SessionStart
  directive and a block in `AGENTS.md`.
- **Incidental:** ADRs 0001 to 0013 get a `Holds` section only when one of
  them misleads an agent, as an amendment.

## Revisit if

- Agents still treat background text as a rule after the reading rule
  ships.
- Teams skip the `Holds` section or fill it with defaults only.

## Context

A report from another session on 2026-09-29 described agents that obey old
ADRs as law. The report traced the cause to the records, not the agents:

- The records write every sentence as a rule. One ADR in that repo said
  `the rule takes no exceptions` and `the layout is fixed, and enforced`.
- The records mix the decision with the mechanism of the day: a Sphinx
  version floor, an nginx regex form, a substring match. An agent gives
  each sentence the same weight.
- Generated text sounds final. A human writes `for now`. A generator writes
  `no exceptions`.
- A PRD and an ADR restate one decision. The PRD kept `status: draft` after
  the work shipped, so the PRD read as a current requirement.
- Records are append-only. A relaxed rule takes a new superseding ADR. Nobody
  writes one, so the stricter text stays in force.

The international team adds weight. A reader of English as a second
language takes absolute wording at face value.

## Decision

An ADR states how strong each part of its decision is.

The ADR template in `domain-modeling/ADR-FORMAT.md` gains three sections:

```md
## Holds

- **Invariant** (test: `tests/test_x.py::test_y`): <what must stay true>.
- **Default:** <what to do unless the task gives a reason>.
- **Incidental:** <how it was built at the time; change freely>.

## Revisit if

- <a condition that reopens the decision>

## Amendments

- YYYY-MM-DD: <what changed and why>.
```

`Holds` sits after the header block. Text outside `Holds` is background. The
background explains the decision; an agent does not follow it as an
instruction. Mechanism detail stays out of `Decision`.

A test marks the hard line: no test, no invariant. `lore lint adr` fails on
an invariant whose test path or test name does not exist. The fix is to add
the test or to downgrade the line to a default.

Agents learn the reading rule from Lore. The SessionStart hook injects one
directive:

> ADRs and PRDs explain where the code comes from. Read them as context.
> Only `Invariant` lines bind, and a test enforces each one. When a task
> conflicts with a default or with an ADR's reasoning, deviate and name the
> deviation in the PR. Propose an amendment when a rule looks out of date.

`lore attach --scaffold-workflow` writes the same text into `AGENTS.md`.

The writing rules gain one rule. ADR and PRD text uses no absolutes outside
an invariant line: `always`, `never`, `no exceptions`, `fixed`, `must`.
A Vale rule flags them and skips code spans.

## Consequences / Trade-offs

- An agent behaves predictably: the tests hold the hard rules, and the rest
  is judgement the agent explains in the PR.
- A relaxed rule costs one dated line, not a new ADR.
- A new ADR costs one more section. The generators in `grilling` and
  the loop wrap-up fill it from the conversation.
- The lint depends on test names staying stable. A renamed test fails the
  lint until the ADR follows the rename.

## Alternatives considered

- **Rewrite every old ADR.** Rejected: most of them did not mislead an agent.
  An amendment on the one that bites costs less.
- **Stop keeping PRDs after the epic closes.** Rejected: the PRD holds the
  scope and the out-of-scope list, which the tracker does not. A shipped
  status and a pointer to the ADR remove the double weight.
- **Mark strength with inline tags in the running text.** Rejected: an
  agent still has to read the whole record to find the tags. One section
  gives one place to look.

## Amendments

- 2026-09-29: `domain-modeling` merged into `grilling` (PRD 0015). The ADR template is `grilling/ADR-FORMAT.md`.
- 2026-09-29: two defaults become invariants. `lore lint adr` and its tests
  landed with PRD 0015. `Revisit if` and `Amendments` stay defaults, because
  the lint checks only `Holds`.
