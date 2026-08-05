---
title: Session-note retirement + flag architecture
status: draft
epic: https://github.com/buchbend/lore/issues/362
repos:
  - buchbend/lore
---

# PRD 0011: Session-note retirement + flag architecture

> Source of truth for this epic. Tracker: [epic issue](https://github.com/buchbend/lore/issues/362).
> The epic links here; this file is not embedded in the issue body.
> Decisions recorded in ADR [0007](../adr/0007-session-notes-retired-flags-are-the-crossing.md),
> ADR [0008](../adr/0008-flag-lands-marked-unreviewed.md),
> ADR [0009](../adr/0009-privacy-boundary-is-locality.md). Design input:
> `brainstorms/lore-session-notes-worth.design.md`.

## Problem

Lore composes a session note for every working session. Almost nobody
reads them. The 2026-08 telemetry sweep counted 17 note pulls per month
across 307 sessions; every pulled note was at most 7 days old, and the
archival reader never appeared. The pipeline costs about 46 LLM calls
per day and produced 60 errors plus 91 force-flushes in 8 days. One
session produced a 1,636-line note holding 6–8 genuine gems. Two PRDs
(0002, 0008) reworked the note mechanism; the noise complaint outlived
both. The team pays a constant LLM and attention tax for a surface it
does not use, while the few genuine gems drown.

## Solution

Retire session notes as vault files. Three layers replace them
(ADR 0007):

- **Team layer** — repo artifacts (ADRs, PRDs, issues, PRs, docs, code)
  and wiki topic/project notes. The lore-workflow chain produces the
  artifacts; the wiki holds only what a human wants to read.
- **Personal layer** — raw transcripts plus the transcript ledger, both
  machine-local by design (ADR 0009). The owner drills their own
  archive; a colleague asks the owner.
- **The crossing** — the flag: one team-relevant fact with a lead
  sentence, short body, and deterministic origin line. An agent files it
  the moment a gem appears. The flag lands in the owning topic note
  immediately, marked unreviewed; the owner reviews pending flags in a
  pull-based walk — accept, retarget, decline, or skip (ADR 0008). The
  banner nudges with a count only. Nothing interrupts a session.

Continuity becomes deterministic: the SessionStart banner renders a
last-active-day recap from the transcript ledger with zero LLM calls.
Capture needs no API key; lore's LLM backend becomes optional.

Rollout is additive-first. The flag primitive and ledger expansion ship
beside the existing pipeline. Measurement (flag rate, review latency,
accept rate, known-gem baseline) is mandatory before trust. The
teardown is the last step, taken with that evidence in hand.

## Implementation decisions

- **Flag write** — one deterministic write verb on the journal pattern:
  CLI verb plus MCP tool, no pipeline, no lore-owned LLM call. Refs in
  the origin line are code-verified at write; uncheckable refs keep
  stamped session-talk phrasing (ADR 0004). Hard gate: no origin, no
  flag. No kind taxonomy; tags optional.
- **Flag landing** — route-before-write: lore proposes the owning topic
  note by search ranking when the caller names none; a new topic note is
  created only when no home exists. Append-only for agents; humans own
  bodies. The unreviewed marker is the single machine-readable pending
  token; pending state is derived by scanning, no queue store
  (ADR 0008). Human-authored flags land unmarked.
- **Sensitivity gate** — evaluates flag text at write time, fails
  closed, quarantines withheld text. After teardown the gate's scope is
  flags only.
- **Transcript ledger** — the existing per-session ledger store gains a
  linkage block: repo, branch, PRs, issues, commits, files. Populated at
  capture time, zero-LLM, derived and rebuildable. Older entries load
  unchanged (one-release back-compat precedent exists).
- **Migration** — one command backfills linkage for the archived
  transcript stock, then deletes the session-note files. Dry-run is the
  default; `--apply` executes. No LLM harvest of the old notes — the
  no-LLM-distills-LLM constraint forbids rescue layers.
- **Retrieval** — drill and search answer "which sessions touched X"
  with transcript pointers, owner-local only. `lore_resume` is removed;
  the context-pack keeps the gather code it imports.
- **Observability** — one spine event per flag write, per review
  verdict, and per ledger-backed read. Status and trace surface the
  counters.
- **Config** — per-wiki compose keys retire with the pipeline; the
  backend key stays optional. Removed keys warn once and are ignored.
- **Retirement discipline** — the teardown slice is HITL: the owner
  gates it on measurement evidence. Until it lands, the shipped
  session-note code keeps running untouched.

## Testing decisions

- Every new path is deterministic, so tests assert exact external
  behavior with no LLM anywhere: flag write → note content, gate
  withhold → quarantine entry, review verdicts → note diffs, ledger
  upsert → entry shape, banner → rendered lines.
- Migration tests run against a fixture vault, never a real one, and
  assert the dry-run plan equals the applied result.
- Back-compat tests load pre-change ledger entries and pre-change wiki
  configs.
- Prior art: the publish-gate scanner tests (fail-closed), ledger
  round-trip tests, and the journal CLI tests.
- The teardown's regression net is the retained-path suites (capture,
  ledger, flags); tests for removed paths are deleted with the code.

## Out of scope

- Briefings. Parked with their config and code dormant; revisit with
  flag-rate evidence (a flag/topic-note digest is the candidate source).
- The freshness-verdict machinery. The review walk is a separate,
  self-contained build.
- Journals, inbox, and the retained frontmatter-only hygiene passes.
- Same-person multi-machine sync. Documented limitation (ADR 0009), not
  a feature.
- LLM harvesting of the retired note stock.
- Vault-side content hygiene (orientation note rewrite) — done by the
  owner directly, not by this epic.
- The 61 legacy concept notes — they stay as read-only stock.
