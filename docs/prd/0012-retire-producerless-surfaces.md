---
title: Retire the producerless surfaces and restore flag transport
status: draft
epic: https://github.com/buchbend/lore/issues/375
repos:
  - buchbend/lore
---

# PRD 0012: Retire the producerless surfaces and restore flag transport

> Source of truth for this epic. Tracker: [epic issue](https://github.com/buchbend/lore/issues/375).
> The epic links here; this file is not embedded in the issue body.

## Problem

Issue #361 removed the compose pipeline in release 0.68.0, merge `f258598`. Epic
#131 severed the surfaces earlier. Both removals deleted writers and kept
readers. The readers now report on records that nothing creates.

All observations below come from host `saiyajin`, at `/home/buchbend/git/lore`,
against `origin/main` at `772e019`.

### A reader cannot tell which status rows are live

`lore status` at 2026-08-05T16:05Z printed six capture rows. Four of them read
retired machinery.

- `Last note` reads a record that no code writes.
- `Last run` counts notes filed by a curator run. The verb is gone.
- `Last flush` reads a store that no code writes.
- `Lock` reads a lock file that no code creates.

### Two alerts fire on healthy systems

- `lore status` labels every hygiene run as curator A. Hygiene runs as role C
  and files no notes. The alert "last 2 runs filed 0 notes" therefore fires on
  every pair of ordinary runs.
- That alert points the reader at `lore trace last`. The selector resolves
  through the flush store. No code writes the flush store. The pointer is dead.
- The pending count never clears. No code stamps a transcript as handled. The
  alert "no hook events in 24h while N transcripts pending" loses its meaning.

### One alert reports a state no code can leave

`lore status` at 2026-08-05T15:58Z printed `running 1` for flushes. A writer
created that record before #361 merged. No code can advance it. The janitor
clears it at the retention horizon.

### Flags never reach a teammate

`lore_core.flag` writes a flag into the wiki and reindexes the wiki. The module
runs no commit and no push. Issue #361 removed the auto-commit hook. The
changelog records the hook as one the compose path alone used. The hook also
committed wiki content, and a flag is now wiki content.

`git_sync.auto_push` has one caller: the briefing command. PRD 0011 parks
briefings. The wiki config defaults `auto_push` to false and `auto_pull` to
true. The read path therefore looks healthy while nothing ships.

### One word names two things

`git_sync` classifies a merge conflict as `ConflictKind.SURFACE` and reads note
directories through `_surface_dirs`. Epic #131 retired a different feature
called surfaces. A reader cannot tell the live concept from the retired one.

## Solution

Lore removes every read surface whose producer is gone. Lore corrects every
surface that reports a state no code can reach. Lore restores the transport
that carries a flag to a teammate.

After this epic:

- Every row `lore status` prints reflects a state some code can write.
- Every alert `lore status` raises names a condition a healthy system does not
  meet.
- A flag reaches a teammate without a manual command.
- The word "surface" names no live concept.
- A resumed orchestrator reads why a feature blocked, not only that it blocked.

## Implementation decisions

### The retained ledger schema

The transcript ledger keeps the fields that live features read:

```
integration      # identity
transcript_id    # identity
path             # sync source
directory        # sync scope, recap grouping
last_mtime       # recap ordering
orphan           # sync skip, recap exclusion
linkage          # drill routing, recap
```

Lore deletes `digested_hash`, `digested_index_hint`, `synthesised_hash`,
`noteworthy` and `session_note`. Lore deletes `TranscriptLedger.advance`. Lore
deletes `WikiLedger` whole.

Transcript sync compares filesystem modification times to decide what to copy.
Sync reads no ledger field to stay idempotent. The deleted fields therefore
carry no sync guarantee.

### The pending set

Lore removes `pending`, `pending_by_wiki` and `_is_pending`. Two callers change:

- The unattached-purge command selects candidates from all entries that carry
  no orphan mark.
- `lore doctor` drops both ledger checks. Each check reports a count and never
  fails the install.

### The drain

Lore reduces the drain event vocabulary to `transcript-synced`. That kind has
the one surviving producer. Lore deletes the drain banner module. Lore severs
the heartbeat's drain read.

The janitor keeps its orphan-prune set. That set targets a pre-#188 file format
and serves an upgrade path.

### The flush store

Lore deletes `begin`, `transition` and `record_failure`. Lore deletes the
flushes panel and the flush selectors in `lore trace`. Lore deletes the seven
dead-letter reason codes that only `record_failure` raised.

The event spine carries a schema version. Removing a closed enum member changes
that schema. The removal therefore bumps the version.

### Flag transport

`lore_core.flag` commits each flag through `lore_core.session.commit_note`. The
function already exists and already commits one note inside a wiki repo.

Lore calls `auto_push` at the session boundary when the wiki carries a remote.
Lore passes no LLM client. A note conflict therefore returns `MERGE_BLOCKED`
and leaves the working tree clean.

`auto_push` defaults to true when a wiki carries a remote. The asymmetric
default is what let the read path look healthy while nothing shipped.

The LLM-merge path stays in the tree, marked parked. The path resolves
conflicts for a push that this epic restores. Deleting the path would cost a
rewrite.

### The rename

`ConflictKind.SURFACE` becomes `ConflictKind.NOTE`. `_surface_dirs` becomes
`_note_dirs`. The `surface_dirs` keyword becomes `note_dirs`.

### The supervision channel

ADR 0002 split supervision state across two stores. The board holds per-feature
state on the epic issue. The epic note held the working narrative on the
orchestrator's session note. Issue #361 deleted the writer for that note.

The board takes the narrative. The board comment gains a notes section below
the table. The table keeps its columns and stays structurally parsed.

### The producer rule

A read surface without a live producer is a defect. A removal that deletes a
writer must delete or rewire every reader in the same change. ADR 0010 records
that rule and supersedes ADR 0002.

## Testing decisions

### The guard test

One test asserts that no declared surface lacks a producer:

- Every drain event kind has an emitter.
- Every spine error code is reachable.
- Every status row has a writer.

The test fails against `772e019` and passes after the removal. The test runs
first, in the red-green order.

Prior art: `tests/test_dead_code_gone.py` guards the removals from #361, and a
test guards the `lore resume` removal from #359.

### Behaviour over implementation

Tests assert what a command prints, not which module produced the line. A test
for the status panel runs the command and reads the output.

### Flag transport

A test files a flag into a wiki repo and asserts the wiki carries a new commit.
A test runs a push against a diverged wiki and asserts the tree returns clean.

Prior art: `tests/test_git_sync.py` builds two hosts and drives a real conflict.

## Out of scope

- The event spine. Every retained producer writes to it.
- `lore_core.quarantine` and `lore_core.note_document`. Both carry live
  callers.
- Briefings. PRD 0011 parks them and this epic does not revive them.
- The janitor's orphan-prune set. The set serves a pre-#188 upgrade path.
- LLM-merge activation. The path stays parked and unreachable.
- PRDs and ADRs as historical record. Only ADR 0002 changes status.
- Issues #123 and #130. Both remain open under epic #131 on a human hold.
