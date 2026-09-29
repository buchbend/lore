# ADR 0014: One home per fact

- **Status:** Accepted
- **Date:** 2026-09-29
- **Deciders:** Christof Buchbender
- **Relates to:** PRD [0015](../prd/0015-slim-workflow-around-homes-and-holds.md);
  extends ADR [0012](0012-agents-file-facts-as-repo-artifacts.md) (filing rule)

## Holds

- **Default:** each kind of fact has one home, per the table below. Other
  artifacts link to the home and do not restate the fact.
- **Default:** a build run ends at a finish point only after the ledger
  check passes. Promote to invariant when the `ledger-check` test lands
  (PRD 0015).
- **Default:** a decision that meets the three ADR criteria of
  `domain-modeling` becomes an ADR. A new domain term enters `CONTEXT.md`.
- **Incidental:** PR bodies and PRDs carry no fixed section skeleton. Each
  takes the shape its task needs.
- **Incidental:** the table names GitHub as the tracker. ADR 0012 keeps
  other backends out of scope.

## Revisit if

- A team reports that a fact has no home in the table.
- Readers keep following links through three or more artifacts to find
  one fact.

## Context

A reader who arrives months later asks three questions. What did the team
set out to do? Why did the team choose it? How does the result work now? Lore and
`lore-workflow` wrote the answers into many places. A PRD and an ADR stated
the same problem and solution. The board notes, the PR body and the epic
issue each carried parts of the narrative. An agent that read all of them
saw a constraint twice and gave it double weight.

PRD 0014 fixed the write side for facts that agents find during a session.
Planned work still had no single map.

## Decision

Lore routes each kind of fact to one home:

| Question a reader asks | Home | Written by |
|---|---|---|
| What did we set out to do, and what is out of scope? | PRD in `docs/prd/` | human and agent in `grilling` |
| Why did we choose X over Y? | ADR in `docs/adr/` | `grilling`, or an agent PR a human merges |
| What does this change do, and where does it deviate from a default? | PR body | agent, free shape |
| What is still open or broken? | issue, label `agent-filed` when an agent files it | agent |
| How does it work now, and how do I use it? | Diátaxis docs | the `document` pass |
| How do agents work in this repo? | `AGENTS.md` | human |
| What does a domain term mean? | `CONTEXT.md`, or the context `CONTEXT-MAP.md` names | `grilling` or a ledger outcome |
| What spans several repos? | wiki topic note, edited through a PR | human merges |
| What shipped, what changed, what is left? | handover section | `build` at its finish point |
| What was said in the session? | transcript copy (personal) | Lore capture |

The trail from code back to intent is a chain of links. `git blame` leads to
the commit, then to the PR, the issue or epic, the PRD and the ADR. A reader
walks the chain without a Lore tool.

Lore enforces the outcome, not the prose. Each build run keeps a ledger of
ADR candidates, term candidates and left-on-the-table items. The ledger
check blocks the finish point until each line has an outcome: approved,
dropped, or filed as an issue.

## Consequences / Trade-offs

- A PR body or a PRD takes any shape. Lore checks only that the decisions
  and terms found in the run reached their home.
- `docs/conventions.md` links to this table and drops its own copy of the
  artifact homes.
- A reader follows links instead of reading one long document. The chain
  stays short: at most four hops from a line of code to its ADR.
- The ledger adds one approval step at each finish point. A run with an
  empty ledger passes the check at once.

## Alternatives considered

- **Fixed PR body with `Why`, `Decisions` and `Follow-ups` sections,
  checked in CI.** Rejected: most changes need no stated reason, and a
  forced section fills with filler. The valuable part is the ADR and the
  glossary entry, not the heading.
- **One long design document per epic.** Rejected: the document ages as
  one block, and a reader cannot tell the current parts from the old ones.
- **No map, and trust each skill's own rule.** Rejected: the skills
  already disagreed. `document-epic`, `quick-feedback-loop` and
  `orchestrate-epic` each wrote reasons to a different place.

## Amendments

None.
