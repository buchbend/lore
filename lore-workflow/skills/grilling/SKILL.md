---
name: lore-workflow:grilling
description: Interview the user relentlessly about a plan or design, and sharpen the domain model as decisions land (CONTEXT.md terms, ADRs). Use when the user wants to stress-test a plan before building, says "grill me" or "grill with docs", uses any other 'grill' trigger phrase, or wants to pin down domain terms or record an architectural decision.
---

# Grilling

**Tier note:** this step runs in the main session at frontier-tier. It is not delegated to a
subagent.

## The interview

Interview the user about every part of the plan until you share one understanding. Walk each
branch of the design tree and resolve the dependencies between decisions one by one. Give your
recommended answer with each question.

Show all questions first: one numbered list, one or two lines each. The user may answer up front
and shorten the grill. Ask back when an answer is ambiguous or confusing; do not take it at face
value. When the user asks to grill slow, ask one question at a time and wait for each answer.

A question the codebase can answer gets a look at the code, not a question.

## Doc-context mode

The user's words pick how much domain modeling runs alongside:

- **Plain ("grill me"), default.** When every question is settled, check whether a term or an ADR
  came up. If so, align with the user and write it.
- **With docs ("grill with docs").** Model the domain from the start. Write each term and ADR as
  the decision lands.

## Sharpen the domain model

Read `CONTEXT.md`, or `CONTEXT-MAP.md` for a repo with several contexts. The layout is in
[CONTEXT-FORMAT.md](CONTEXT-FORMAT.md).

- **Challenge against the glossary.** A term that conflicts with `CONTEXT.md` gets called out at
  once: "The glossary defines 'cancellation' as X, but you seem to mean Y. Which is it?"
- **Sharpen fuzzy words.** Propose one precise term for a vague or overloaded one.
- **Test with scenarios.** Invent edge cases that force a precise boundary between two concepts.
- **Cross-check the code.** When the user states how something works, check the code. Name a
  contradiction.

## Write terms and ADRs

Run `lore style show writing-rules` before you write a term or an ADR.

- **Terms.** Propose the wording and wait for the user's yes. A person approves every glossary
  entry before it is written. Write it into `CONTEXT.md` right away, per
  [CONTEXT-FORMAT.md](CONTEXT-FORMAT.md). `CONTEXT.md` is a glossary only: no implementation
  detail, no spec, no scratch notes. Create the file lazily, with the first term.
- **ADRs, sparingly.** Offer one only when the decision is hard to reverse, surprising without
  context, and the result of a real trade-off. All three, or skip it. Write it per
  [ADR-FORMAT.md](ADR-FORMAT.md): fill `Holds` and `Revisit if` from the interview. Sort each part
  of the decision into invariant, default or incidental. An invariant names the test that
  enforces it; without a test, write a default.

## End

List every term you wrote or changed: one line per term, quoting the term and its definition.
When you wrote none, say so plainly instead of printing an empty list.

Then hand off: [`to-epic`](../to-epic/SKILL.md) for several features, or
[`build`](../build/SKILL.md) in `issue` mode for one change. File that issue through
[`file-issue`](../file-issue/SKILL.md) first.
