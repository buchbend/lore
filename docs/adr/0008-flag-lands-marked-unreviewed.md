# ADR 0008: A flag lands in the wiki at write time, marked unreviewed

- Status: Accepted
- Date: 2026-08-05
- Context: grilling session 2026-08-05 on
  `brainstorms/lore-session-notes-worth.design.md`,
  PRD [0010](../prd/0010-session-note-retirement-flag-architecture.md)

## Context

A flag is LLM-authored text on a human surface, and every such line is
a poisoning surface. Teammates need to see fresh flags fast. The author
needs a review step that never blocks a session.

## Decision

- The agent files the flag. The sensitivity gate checks the flag text
  at write time and fails closed.
- Lore appends the flag block to the owning topic note immediately.
  The origin line ends with the unreviewed marker. When no home note
  exists, lore creates the proposed topic note, also marked.
- A human-authored flag lands without the marker.
- The review walk (`lore flag review`) presents each pending flag.
  Accept removes the marker. Retarget moves the block to another note.
  Decline deletes the block. Skip leaves the flag pending.
- Lore derives pending state by scanning notes for the marker. No
  queue store exists.
- The SessionStart banner shows a count of pending flags. The banner
  never shows flag content and never blocks.
- One spine event records each flag write and each review verdict.

## Consequences / Trade-offs

Easier: teammates see a fresh flag immediately, labelled as
unreviewed. No queue store can drift. Git history keeps declined
flags.

Harder: unreviewed LLM text is visible on the team surface until the
owner reviews. Review latency replaces under-flagging as the silent
failure, so measurement covers flag rate, review latency, and accept
rate.

## Alternatives considered

- Staged queue under `.lore/`, with the wiki write on accept.
  Rejected: flags stay invisible until the owner walks the queue, and
  queue rot hides gems.
- Reuse the freshness-verdict walk machinery. Rejected: the owner
  wants a small self-contained build, not a coupling to verdicts.
- Direct append without a marker. Rejected: LLM-authored vault text
  needs a human review point.

## Status

Accepted.
