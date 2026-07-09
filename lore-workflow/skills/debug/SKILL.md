---
name: lore-workflow:debug
description: Systematic root-cause debugging with a hard circuit breaker. Use when a bug resists fixing, a test keeps failing, a teammate is stuck after repeated fix attempts, or you catch yourself guessing at fixes instead of finding the cause.
---

# Debug

The building discipline is `/lore-workflow:tdd`. This is its diagnostic counterpart: a phased
process for finding the **root cause** of a bug instead of guessing at fixes,
plus a hard circuit breaker that stops a doomed fix loop before it wastes a
whole session.

The failure mode this skill exists to prevent: patching the **symptom** you can
see (the red test, the stack trace, the wrong number) without ever locating the
**fault** that produced it — so the bug moves, reappears, or splits into two.

## Phased process

Work the phases in order. Do not skip ahead to a fix; a fix you can't explain is
a guess, and guesses are what the circuit breaker below exists to catch.

### 1. Reproduce

Get a reliable, minimal reproduction first. A bug you cannot trigger on demand,
you cannot prove you fixed — you can only convince yourself. Shrink it: the
smallest input, the fewest steps, the tightest test that still shows the fault.
A minimal reproduction is often already half the diagnosis.

### 2. Observe

Read the actual evidence before theorizing — the real error message, the full
stack trace, the failing assertion, the logs, the diff since it last worked. Do
not skim the first line and pattern-match. Separate the **symptom** (what you
observe) from the candidate **fault** (what is actually wrong); they are rarely
the same line.

### 3. Hypothesize

Form ONE falsifiable hypothesis about the root cause, and state what must be true
if it holds. "The timestamp is UTC but compared as local" is a hypothesis you can
test; "something's wrong with the dates" is not. One hypothesis at a time — a
scattershot of simultaneous guesses is how the attempt counter below runs out.

### 4. Isolate

Test the hypothesis with the cheapest decisive experiment. Bisect the change
history, binary-search the input, add one targeted log line or assertion, or
disable half the pipeline — whatever localizes the fault fastest. Each experiment
must be able to come back *no*; an experiment that can only confirm your
hypothesis proves nothing. Narrow until the fault sits on a known line.

### 5. Fix the root cause

Fix the fault the isolation step localized, not the symptom. If a change makes
the symptom disappear but you cannot say *why* it was failing, you have written a
patch, not a fix — the bug is still there, now hidden. Prefer the fix that makes
the whole class of bug impossible over the one that silences this instance.

### 6. Verify

Confirm the symptom is gone against the reproduction from phase 1, then add a
regression test that fails without the fix (hand this to `/lore-workflow:tdd`). Finally check
you did not just move the bug: re-run the surrounding suite and reason about what
else touched the fault.

## Circuit breaker

Count your fix attempts on a single bug. **After 3 failed fix attempts, STOP.**
Do not attempt a 4th.

Three failures on the same fault is not bad luck — it is evidence that your model
of the system is wrong. The bug is not where you keep looking, or the design
makes the correct fix unreachable from where you are standing. A 4th attempt from
the same mental model is very likely to fail the same way, and each blind retry
digs the hole deeper.

When the breaker trips, change altitude instead of trying again: **question the
architecture, not the line.**

- State, in writing, what you *assumed* was true that the three failures
  contradict. The wrong assumption is the actual bug.
- Widen the frame: is the fault in a different module, a shared dependency, the
  data, the environment, or the interface contract rather than the code you keep
  editing?
- Ask whether the architecture itself is forcing the bug — a leaky abstraction, a
  missing seam, two components that should not be coupled. The right fix may be a
  design change, not a code change.
- If you are a teammate under a supervisor, this is the point to escalate with
  what you have ruled out — not to spend the session on a 4th, 5th, 6th attempt.

Resetting the counter is legitimate only when you have genuinely new information
(a new reproduction, a disproven assumption, a different hypothesis) — not when
you simply want to keep trying the same thing.
