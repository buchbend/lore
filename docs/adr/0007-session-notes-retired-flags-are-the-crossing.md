# ADR 0007: Session notes are retired; the flag is the only session-to-wiki crossing

- Status: Accepted
- Date: 2026-08-05
- Context: design export `brainstorms/lore-session-notes-worth.design.md`,
  PRD [0010](../prd/0010-session-note-retirement-flag-architecture.md);
  supersedes ADR 0001 and ADR 0003

## Context

Telemetry from the 2026-08 sweep (recorded in
`brainstorms/lore-session-notes-worth.md`):

- Readers pulled session notes 17 times per month across 307 sessions.
  Every pulled note was at most 7 days old.
- The note pipeline cost about 46 LLM calls per day. Eight days of runs
  produced 60 errors and 91 force-flushes.
- The context-finder tools out-used note retrieval about 4.5 to 1.
- `lore_resume` was never called.
- One session produced a 1,636-line note holding 6–8 genuine gems.

PRD 0002 and PRD 0008 each reworked the note mechanism. The noise
complaint outlived both. The mission was wrong, not the mechanism.

## Decision

Three layers replace the session-note pipeline:

- Team layer: repo artifacts (ADRs, PRDs, issues, PRs, docs, code) and
  wiki topic/project notes plus flags.
- Personal layer: raw transcripts and the transcript ledger,
  machine-local (ADR 0009).
- Crossing: the flag — one stamped fact appended to the owning topic
  note (ADR 0008).

Retired: the Curator A compose path, the in-note fact ledger, note
renders, `sessions/*.md` growth, and `lore_resume`. The SessionStart
recap renders from the transcript ledger with zero LLM calls. Lore
deletes the existing session notes and backfills the ledger from the
archived transcripts. Rollout is additive-first: the flag primitive
ships beside the pipeline; teardown is the last step, taken with
flag-rate measurement in hand.

## Consequences / Trade-offs

Easier: capture runs with zero LLM calls and needs no API key. The
sensitivity gate checks only flag text. The daily LLM spend of the
pipeline disappears.

Harder (losses accepted by the owner, 2026-08-04): passive teammate
browsing of session notes is gone. Briefings lose their source and are
parked. Onboarding quality rides on flag quality. Under-flagging is
invisible without measurement, so measurement is mandatory before
teardown.

## Alternatives considered

- Rework the note mechanism again. Rejected: PRD 0002, PRD 0008, and a
  prior brainstorm tried; the complaint persisted.
- Harvest gems from the note stock with an LLM pass. Rejected: the
  no-LLM-distills-LLM constraint forbids rescue layers.
- Keep notes as an optional mode. Rejected: an unused surface still
  costs maintenance, gate scope, and trust.

## Status

Accepted. Supersedes ADR 0001 (reopen semantics) and ADR 0003 (derived
note body); both describe retired machinery.
