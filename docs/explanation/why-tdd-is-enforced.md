# Why test-first development is enforced

`lore-workflow` makes **test-driven development** (TDD) a hard rule, not a
suggestion: every implementation teammate writes a failing test first, makes
it pass, then refactors — the red → green → refactor loop (`lore-workflow:tdd`)
— and no pull request merges on red. This page explains why the discipline
is mandatory rather than left to judgement.

## The problem it solves

Most of the code in this workflow is written by autonomous teammate agents
you are not watching. That changes the economics of a dropped test. When a
human writes code and skips the test, the human still has the mental model
of what the code should do and can catch a regression by eye. An agent that
skips the test leaves **no such safety net** — the next agent to touch the
file has only the code, and the code cannot tell you whether it does what
was *meant*, only what it *does*.

Tests written **first** pin the intended behaviour before the implementation
exists to bias them. Tests written after the fact ratify whatever the code
already does — including its bugs. That difference is the whole point: the
failing test is a specification the code must satisfy, and it is only a
specification if it comes first.

## Why it is enforced, not encouraged

The moments where a teammate is most tempted to skip the test are exactly
the moments where skipping costs the most:

- *"This is just a mechanical change."* Trivial edits are where a typo ships
  unnoticed, because nobody looks hard at a one-liner.
- *"I'll add tests after."* After-the-fact tests ratify the code you wrote,
  not the behaviour you meant.
- *"The skill is overkill here."* The red → green loop is the floor, not
  ceremony reserved for hard problems.

Because these excuses are predictable, the workflow names them explicitly in
the teammate brief and closes the door on them, rather than trusting each
agent to resist them in the moment. A green pull request with no test
mapping to its acceptance criteria is not accepted.

## How it shows up in the chain

- On the [epic chain](../how-to/run-an-epic.md), every fan-out teammate
  follows `lore-workflow:tdd`, and each feature's crosscheck verifies the
  tests map to the acceptance criteria before the pull request merges.
- On the [fast path](../how-to/use-the-fast-path.md), the same strict loop
  is one of the invariants the single-issue track keeps even though it drops
  the fan-out and multi-PR integration.

Enforcing the loop everywhere is what lets the autonomous build be trusted
at all: the tests are the standing evidence that the shipped behaviour is
the intended behaviour, produced by the same process no matter who — or
which agent — wrote the code.
