---
title: Deterministic context, lights-out notes
status: draft
epic: https://github.com/buchbend/lore/issues/162
repos:
  - buchbend/lore
---

# PRD 0004: Deterministic context, lights-out notes

> Source of truth for this epic. Tracker: [epic issue](https://github.com/buchbend/lore/issues/162).
> The epic links here; this file is not embedded in the issue body.

## Problem

Lore's curator had three ambitions: (A) lights-out session notes, (B) curating
decisions out of transcripts, and (C) cross-note elevation of concepts. Long
experiments showed B and C are too dangerous: every auto-promoted statement
becomes ambient context for future sessions, and one wrong statement derails
them. With small local models, bullet-proof extraction is not attainable now.

Meanwhile the retrieval side is weaker than it needs to be: relevance is
FTS/recency search, although transcripts deterministically contain the repo,
branch, issue/PR/epic numbers, files touched, and commits — the exact join
keys that would make "which past sessions and ADRs/PRDs matter right now" a
cheap, trustworthy lookup. Building session context still costs explorer
subagent runs that a deterministic pointer pack could replace.

Sharing is also underspecified: notes enter under the system username, there
is no explicit consent moment when a repo starts routing to a shared vault,
and multi-author sync of a shared wiki is unverified.

## Solution

Lore narrows to what it can do trustworthily: **deterministic context
plumbing plus lights-out session notes**, serving humans and AI (via MCP)
alike. Humans never trigger lore manually.

- **Curator B and C are deleted.** Decisions enter context only through the
  ratified channel: workflow-written ADRs/PRDs in the connected repo, pulled
  on demand. Curator A (session-note compose) stays fully automatic.
- **Compose is hardened structurally, not with a bigger model**: a
  deterministic skeleton (refs, commits, files touched, decisions quoted
  verbatim from the transcript) carries the trustworthy content; the small
  model writes only the connecting narrative; the publish gate is unchanged
  and fail-closed.
- **Deterministic linkage frontmatter** at capture: repo, branch, issue/PR/
  epic refs, files touched, commits, author display name — zero LLM cost.
- **`lore_context_pack` MCP tool**: given cwd/branch/issue, return a pointer
  pack — recent session notes for this scope, ADRs/PRDs referenced by them or
  co-touching the same files, open epic state — pointers plus one-line
  summaries, bodies pulled selectively. This is the deterministic front door
  that planning skills call before spawning any explorer.
- **Team model**: minimal use is private — nothing leaves the machine.
  Routing a repo to a shared vault is an explicit opt-in config act, and that
  moment carries the consent prompt (notes will be committed and
  team-visible; the gate reduces but cannot eliminate leaks; a leaked secret
  persists in git history — the remedy is rotation). Composed, gate-passed
  notes are committed to the shared wiki; transcripts and buffers never are.
  Onboarding asks for a display name. Briefings become the compression
  channel: briefing → notes → ADRs/PRDs/issues/code, all pull-only.

## Implementation decisions

- Delete `lore_curator` B/C paths (decision curation, concept elevation) and
  their tests; keep buffer/flush/compose (A) and hygiene where it serves A.
- Linkage extraction is adapter-level and deterministic: parse tool calls and
  git state from the transcript/session environment; store as structured
  frontmatter on the session note (schema versioned).
- Relevance in `lore_context_pack` is a join on linkage keys (scope, repo,
  files, epic), backed by the existing `repo_docs` pull for ADR/PRD homes —
  never an LLM call, never ambient injection; the tool returns pointers.
- Sharing consent lives in the attach/routing flow; per-scope routing config
  decides which vault composed notes commit to. Author display name is vault
  config, not `$USER`.
- Multi-author sync hardening happens in the existing `git_sync` machinery:
  lights-out pull/rebase/push with conflict-free note naming
  (author + session id), verified under concurrent writers.

## Testing decisions

- Linkage frontmatter: fixture transcripts per adapter → exact expected
  frontmatter (golden files); schema-version round-trip.
- Compose skeleton: deterministic sections must be byte-stable given the same
  buffer; narrative slots are the only free text; gate tests unchanged and
  extended for the skeleton shape.
- `lore_context_pack`: fixture vault + fixture repo with ADRs/PRDs → expected
  pointer pack for given cwd/branch/issue; empty/cold-start cases.
- Multi-author sync: two simulated writers on one wiki repo, interleaved
  flushes — no lost notes, no manual intervention.
- Deletion slice: test suite green with B/C tests removed; no dead imports
  (ruff) — proves the cut is clean.

## Out of scope

- Any re-introduction of auto-promoted decision/concept extraction (a
  human-facing digest may return later, never as ambient context).
- Workflow skill changes consuming `lore_context_pack` (follow-up lightening
  epic; PRD 0003 covers the migration).
- Per-host spawn enforcement, briefing sinks beyond the existing channels.
