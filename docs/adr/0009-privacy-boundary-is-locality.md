# ADR 0009: The privacy boundary is locality

- Status: Accepted
- Date: 2026-08-05
- Context: archive audit in `brainstorms/lore-session-notes-worth.md`,
  PRD [0010](../prd/0010-session-note-retirement-flag-architecture.md)

## Context

The 2026-08 archive audit counted 556 transcripts (216 MB), all
gitignored, all on one machine. That privacy was accidental. The
three-layer architecture (ADR 0007) needs the boundary to be
structural.

## Decision

- Transcripts and the transcript ledger stay machine-local and
  gitignored by design.
- Lore ships no sync, no backup, and no cross-machine transcript
  access.
- The owner drills their own archive. A colleague asks the owner.
- Same-person multi-machine use is a documented limitation, not a
  feature gap.
- Personal backup of the archive is the developer's own concern.

## Consequences / Trade-offs

Easier: no access control, no encryption, no server. The wedge
phrasing stays honest — the raw why never leaves the author's machine.

Harder: a lost machine loses that machine's raw record. The ledger
rebuilds from git and GitHub; transcript text does not.

## Alternatives considered

- Encrypted synced transcript store. Rejected: key management and a
  sync target contradict the lightweight-or-unused constraint.
- Transcripts inside the wiki git repo. Rejected: raw transcripts on
  the team surface break the privacy boundary outright.
- A lore-owned private backup repo per user. Rejected: backup is not
  lore's job, and the repo would still need access control.

## Status

Accepted.
