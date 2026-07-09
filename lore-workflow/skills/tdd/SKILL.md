---
name: lore-workflow:tdd
description: Test-driven development with red-green-refactor loop. Use when user wants to build features or fix bugs using TDD, mentions "red-green-refactor", wants integration tests, or asks for test-first development.
---

# Test-Driven Development

## Philosophy

**Core principle**: Tests should verify behavior through public interfaces, not implementation details. Code can change entirely; tests shouldn't.

**Good tests** are integration-style: they exercise real code paths through public APIs. They describe _what_ the system does, not _how_ it does it. A good test reads like a specification - "user can checkout with valid cart" tells you exactly what capability exists. These tests survive refactors because they don't care about internal structure.

**Bad tests** are coupled to implementation. They mock internal collaborators, test private methods, or verify through external means (like querying a database directly instead of using the interface). The warning sign: your test breaks when you refactor, but behavior hasn't changed. If you rename an internal function and tests fail, those tests were testing implementation, not behavior.

## Anti-Pattern: Horizontal Slices

**DO NOT write all tests first, then all implementation.** This is "horizontal slicing" - treating RED as "write all tests" and GREEN as "write all code."

This produces **weak, implementation-coupled tests**:

- Tests written in bulk test _imagined_ behavior, not _actual_ behavior
- You end up testing the _shape_ of things (data structures, function signatures) rather than user-facing behavior
- Tests become insensitive to real changes - they pass when behavior breaks, fail when behavior is fine
- You commit to test structure before understanding the implementation

**Correct approach**: Vertical slices via tracer bullets. One test → one implementation → repeat. Each test responds to what you learned from the previous cycle. Because you just wrote the code, you know exactly what behavior matters and how to verify it.

```
WRONG (horizontal):
  RED:   test1, test2, test3, test4, test5
  GREEN: impl1, impl2, impl3, impl4, impl5

RIGHT (vertical):
  RED→GREEN: test1→impl1
  RED→GREEN: test2→impl2
  RED→GREEN: test3→impl3
  ...
```

## Skip-excuses

Under time pressure the discipline gets dropped with a stock rationalization. Name it as you hear yourself think it — that is the tell that you are about to cut the corner, not a reason to:

- **"This is just a mechanical change."** The changes that look too trivial to test are exactly where an unguarded typo ships — a flipped comparison, an off-by-one, a wrong constant. Mechanical is cheap to test; test it.
- **"I'll add tests after."** Tests written after the code test the code you wrote, not the behavior you meant; they ratify bugs instead of catching them, and "after" rarely comes. The failing test comes first — that is the whole method.
- **"The skill is overkill here."** The red→green loop is the floor, not a ceremony reserved for hard problems. If the change is small the loop is small; it is never so small that skipping it is faster than the bug it prevents.

## Workflow

### 1. Planning

When exploring the codebase, use the project's domain glossary so that test names and interface vocabulary match the project's language, and respect ADRs in the area you're touching.

Before writing any code, resolve these gates. How you resolve them is **mode-conditional**:

- **Interactive** (a user is present to respond): ask, and wait for an answer, at each gate below.
- **Autonomous** (e.g. a teammate implementing a sub-issue with no user available to ask): decide each gate from the sub-issue's acceptance criteria and the codebase's existing conventions, then record the decisions in the PR body under a "Planning decisions" heading so a reviewer can see what was chosen and why.

Gates:

- [ ] What interface changes are needed
- [ ] Which behaviors to test (prioritize)
- [ ] Identify opportunities for deep modules (small interface, deep implementation)
- [ ] Design interfaces for testability
- [ ] List the behaviors to test (not implementation steps)
- [ ] Plan is approved — interactive: by the user; autonomous: self-certified against the acceptance criteria, recorded in the PR body

Interactive prompt: "What should the public interface look like? Which behaviors are most important to test?"

**You can't test everything.** In interactive mode, confirm with the user exactly which behaviors matter most. In autonomous mode, prioritize the critical paths and complex logic implied by the acceptance criteria, not every possible edge case — and record that prioritization alongside the other planning decisions.

### 2. Tracer Bullet

Write ONE test that confirms ONE thing about the system:

```
RED:   Write test for first behavior → test fails
GREEN: Write minimal code to pass → test passes
```

This is your tracer bullet - proves the path works end-to-end.

### 3. Incremental Loop

For each remaining behavior:

```
RED:   Write next test → fails
GREEN: Minimal code to pass → passes
```

Rules:

- One test at a time
- Only enough code to pass current test
- Don't anticipate future tests
- Keep tests focused on observable behavior

### 4. Refactor

After all tests pass, look for refactor candidates:

- [ ] Extract duplication
- [ ] Deepen modules (move complexity behind simple interfaces)
- [ ] Apply SOLID principles where natural
- [ ] Consider what new code reveals about existing code
- [ ] Run tests after each refactor step

**Never refactor while RED.** Get to GREEN first.

## Checklist Per Cycle

```
[ ] Test describes behavior, not implementation
[ ] Test uses public interface only
[ ] Test would survive internal refactor
[ ] Code is minimal for this test
[ ] No speculative features added
```
