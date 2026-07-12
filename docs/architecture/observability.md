# Observability: one spine, three commands

**Audience:** contributors debugging why a note didn't appear, adding a
new spine producer, or wondering why `lore log`/`lore news`/`lore runs`/
`lore proc` no longer exist as commands.

Before this epic, debugging one background flush meant walking seven
uncorrelated surfaces — `lore log`, `lore proc`, `lore runs`, `lore
status`, `lore news`, `lore doctor`, and a crash-log directory — over
three different ad-hoc file schemas, with no identifier tying a hook fire
to the curator run it spawned to the note it published. Failure handling
leaned on "availability over debuggability" past the point of usefulness:
drain writes never raised, mid-session flush failures deferred silently,
retention cleanup swallowed its own errors. The full problem statement
lives in [PRD 0005](../prd/0005-onboarding-config-observability.md).

The replacement is one append-only event log (the **spine**) that every
background producer writes to, one **trace_id** that follows a flush from
hook fire to published note, one **flush state machine** that makes
"stuck" a queryable state instead of an absence of evidence, and **three**
read commands instead of seven.

---

## The event spine

**Module:** `lore_core/spine.py`. All background-pipeline telemetry is
appended to `$LORE_ROOT/.lore/spine.jsonl` through one writer,
`SpineWriter.emit()`, in one envelope shape:

```
{ ts, v, source, event, level, trace_id, session_id, run_id,
  wiki, scope, error_code, data }
```

- **`source`** — the producer, one of the closed `SOURCES` set:
  `hook`, `curator`, `drain`, `janitor`, `install`. A source outside this
  set is a bug, not data — `validate_envelope()` rejects it.
- **`level`** — `info` / `warn` / `error`.
- **`error_code`** — `None`, or a value from the closed `ErrorCode`
  `StrEnum`. Free-form detail (exception type, message, offending path)
  belongs in `data`, never in `error_code` directly.
- Fields a producer can't yet know (`trace_id` before a flush exists,
  `run_id` before a run starts, …) are written as explicit `null`, never
  omitted — readers never have to guess "unknown" from "field dropped".

**Producers today:**

| `source` | Module(s) | What it emits |
|---|---|---|
| `hook` | `lore_cli/hooks.py`, `lore_core/spine.py:emit_hook_event` | SessionStart/PreCompact/Stop/UserPromptSubmit/SessionEnd/Capture hook fires and their outcomes |
| `curator` | `lore_core/run_log.py`, `lore_core/flush_store.py`, `lore_curator/chapter_flush.py` | Curator run decisions (`run-start`, `transcript-start`, `noteworthy`, `session-note`, `run-end`), flush state-machine transitions, spawn failures |
| `drain` | `lore_core/drain.py` | Session-scoped "what Lore did" events (`note-filed`, `note-appended`, `surface-proposed`, `transcript-synced`) — read by `lore status`'s news section |
| `janitor` | `lore_core/janitor.py`, `lore_core/run_retention.py`, `lore_cli/_crash_log.py` | Retention deletions/downgrades and their failures |
| `install` | *(reserved, no producer yet)* | Onboarding-wizard events (PRD 0005 pillar C); not wired as of this writing — `lore init` doesn't emit to the spine today |

**Schema-version policy:** `SCHEMA_VERSION` (currently `1`) bumps on any
change to the envelope *shape* — a field added, removed, renamed, or a
semantic change to an existing field. Adding an `ErrorCode` value is
additive and does **not** bump the version; removing or renaming one
does. Readers must skip an unfamiliar `v`, never crash on it.

**Concurrency:** appends are POSIX-atomic — `emit()` opens the file
`O_APPEND | O_CREAT` and writes one JSONL record in a single `os.write()`
call, and every record stays well under `PIPE_BUF` (4096 bytes on
Linux), so concurrent sessions append lock-free. Rotation (hot spine
crossing its size cap) takes a non-blocking `flock` on a sibling
`spine.rotate.lock`; a losing writer just skips rotation this cycle and
retries on its next emit. A write failure never raises — it touches
`spine-failed.marker` so `lore doctor`/`lore status` can surface "spine
writes are failing" without crashing the hot path. Full guarantee list:
[`docs/architecture/state.md`, "Concurrency-safety guarantees"](state.md#concurrency-safety-guarantees).

---

## trace_id — one flush, one story

**Minted by:** `lore_core/spine.py:new_trace_id()` (an opaque
`secrets.token_hex(8)`), called from
`lore_curator/chapter_flush.py:spawn_detached_flush` when a hook decides
a detached flush needs to run.

**Lifecycle:**

1. A hook (or the reaper's startup sweep) decides a buffer needs
   flushing and calls `spawn_detached_flush`, which mints a trace_id
   (unless one is already threaded in).
2. The trace_id crosses the process boundary as `LORE_TRACE_ID` in the
   detached `lore curator flush` subprocess's environment — the *one*
   place it's handed off (`chapter_flush.py:spawn_detached_flush`).
3. `lore_cli/curator_cmd.py` reads `LORE_TRACE_ID` and stamps it onto
   every `source="curator"` run event the flush emits (`run-start`,
   `noteworthy`, `session-note`, `run-end`, …).
4. The same trace_id is passed to `DrainStore.emit()` for any
   `source="drain"` event the flush produces (`note-filed`,
   `note-appended`, …).
5. If the flush publishes a note, the trace_id is written into the
   note's `linkage.trace_id` frontmatter field
   (`lore_core/note_document.py:_apply_linkage`) — composing with PRD
   0004's deterministic linkage block, so the note itself is a valid
   trace selector.

`lore trace <selector>` (below) reads every spine record sharing a
trace_id, sorted by timestamp — there is no separate correlation index,
the trace *is* the filtered spine.

Spawn failures are traceable too: `spawn_detached_flush` mints its
trace_id before taking the spawn lock, so both failure paths — the
subprocess failing to start at the OS level and the spawn-lock flock
itself erroring — emit `source="curator"`, `event="flush-spawn-failed"`
with that trace_id attached, making the event reachable via
`lore trace` like every other step of the flush's story.

---

## The flush lifecycle state machine

**Module:** `lore_core/flush_store.py` (issue #189). One persisted
`FlushRecord` per flush unit (keyed by buffer stem) replaces "retry
forever, or silently give up with no marker":

```
queued -> running -> published | withheld | dead-lettered(reason)
```

- `running -> queued` is a **scheduled retry**, not a self-loop; terminal
  states (`published`, `withheld`, `dead-lettered`) have no outgoing
  edges — `is_legal_transition()` is the source of truth, checked by
  `tests/` against the full transition table.
- Bounded retries: `MAX_ATTEMPTS = 3`, exponential backoff
  (`backoff_seconds()`, base 60s, capped at 3600s). Exhaustion produces a
  `dead-lettered` record with a structured reason instead of silence.
- Every transition also emits a `source="curator"`,
  `event="flush-<state>"` spine record, so the record is the *current*
  queryable state and the spine is the *history* — a record reopened for
  a new unit never erases the trail.
- Every previously-silent failure path in the flush pipeline (buffer
  sidecar read errors, spawn failures, chapter-append I/O errors) now
  either emits an error-level spine event or produces a dead letter —
  see the `ErrorCode` values `COMPOSE_FAILED`, `SPAWN_FAILED`,
  `SIDECAR_READ_FAILED`, `CHAPTER_APPEND_FAILED` in `spine.py`.

---

## Retention — one janitor, tiered and visible

**Module:** `lore_core/janitor.py` (issue #190). One flock-guarded sweep
(`janitor_lock`, non-blocking — a contended lock just skips this cycle)
runs opportunistically from hook fire / curator run end
(`lore_cli/_janitor_entry.py:run_opportunistic_janitor`) — no daemon.

| Family | Tier / cap | Outcome when exceeded |
|---|---|---|
| Spine — hot (`spine.jsonl`) | age > `retention.hot_days` (7d) or size > `hook_events.max_size_mb` (10MB) | downgrade: rotate to `spine.jsonl.1` |
| Spine — cold (`spine.jsonl.1`) | age > `retention.cold_days` (30d) or size > `retention.cold_max_mb` (20MB) | delete outright — no tier below cold |
| Legacy run-archival files | `runs.keep` (200) / `runs.max_total_mb` (100MB) / `runs.keep_trace` (30) | age/count-capped delete (`lore_core/run_retention.py`) |
| Flush store | terminal records older than `retention.cold_days`; dead letters exempt from age but hard-capped at `retention.dead_letter_hard_cap` (50) | purge (`FlushStore.purge`) |
| Crash logs (`$LORE_CACHE/crashes/`) | `retention.crash_log_days` (30d) | purge (`lore_cli/_crash_log.py`) |
| Legacy drain orphans (`.lore/drain/_system.jsonl`) | rows whose referenced note path no longer exists | drop (`lore_core/janitor.py:prune_orphans`) — upgrade cleanup only; nothing writes this file post-#188 (drain events live on the spine, `source="drain"`, and get the same tiered retention as everything else above) |

Every deletion and tier-downgrade emits a `source="janitor"` spine
event (`retention-delete` / `retention-downgrade`); a delete failure
emits a `warn`-level `retention-delete-failed` event instead of failing
silently. The last pass's usage snapshot is queryable via
`read_janitor_status()` — that's what `lore status`'s retention section
reads.

---

## The three commands

Seven read surfaces became three. Each names its own drill-down target
so a reader never has to guess where to look next.

### `lore status`

**Module:** `lore_cli/status_cmd.py`. The single glanceable "is Lore
healthy right now?" dashboard — capture liveness (last note, last run,
last flush, last hook fire, lock state), flush queue counts (queued /
running / dead-lettered), per-wiki connection health (dirty / ahead /
behind / reachable), retention usage, an absorbed **news** section
(session + background drain events — the old `lore news`), and an
alerts section where every warning names its exact drill-down command.
Reads only; exit code is 0 when healthy, nonzero when any alert fires.

### `lore trace <selector>`

**Module:** `lore_cli/trace_cmd.py`, business logic in
`lore_core/trace.py`. Renders the chronological, correlated story of one
flush — every spine record sharing a trace_id, as a Rich tree (or `
--plain` aligned text, or `--json` raw JSONL). Absorbs the debugging role
of the old `lore log` / `lore runs` / `lore proc`. The selector accepts a
trace_id, a session_id (resolves every trace_id that session touched,
newest first), `last`, `dead` (lists dead-lettered flushes instead of one
tree), or a note path / `[[wikilink]]` (reverse-resolved through the
note's own `linkage.trace_id`).

### `lore doctor [--fix]`

**Module:** `lore_cli/doctor_cmd.py`. Deep install-integrity diagnostics
— `LORE_ROOT` resolution, wiki presence, cache writability, the
SessionStart hook path, MCP/search-backend imports, the Claude Code
plugin-cache version (a *failing* check, not advisory), Cursor
integration checks, and more — one line per check, exit nonzero on any
failure. `--fix` additionally repairs: rebuilds `scopes.json` from
accepted attachments, re-stamps drifted `.lore.yml` offer fingerprints,
and migrates attachment path prefixes after a vault/repo move. Every
repair is individually declinable (prompted unless `--yes`); plain
`doctor` (no `--fix`) never writes state.

**Known rendering quirk:** `--fix` repairs run and print their
human-readable confirmation prompts/receipts *before* the `--json`
envelope is emitted (`_run_repairs` runs ahead of the `if json_out`
branch in the callback) — `lore doctor --fix --json` therefore
interleaves plain text ahead of the JSON on stdout. Script against
`lore doctor --json` (no `--fix`) instead, or pass `--yes` and parse only
the trailing JSON line.

### Deprecated: `log`, `news`, `runs`, `proc`

Kept as thin aliases for one minor-version window (see the CHANGELOG for
the exact removal version): each prints a one-line pointer to its
replacement on **stderr** (so `--json` output on stdout stays script-safe)
and then still runs its original logic. `lore drain prune` had no
replacement to alias to — it pruned a file (`.lore/drain/_system.jsonl`)
that has had no writer since drain events moved onto the spine, so its
CLI surface was removed outright; `lore_core.janitor.prune_orphans`
still exists and runs automatically as part of every opportunistic
janitor pass.

---

## See also

- [PRD 0005](../prd/0005-onboarding-config-observability.md) — the full
  problem statement and every implementation decision.
- [`session-note-lifecycle.md`](session-note-lifecycle.md) — "How
  failures surface" ties the state machine above to what a reader
  actually sees when a note doesn't appear.
- [`state.md`](state.md) — concurrency guarantees shared with the spine,
  and the three state files this doc doesn't cover (`_scopes.yml`,
  `scopes.json`, `attachments.json`).
- [`cli-contract.md`](cli-contract.md) — the shape every `lore <verb>`
  command follows, including these three and the deprecated aliases.
- How-to: [`../how-to/onboarding.md`](../how-to/onboarding.md),
  [`../how-to/troubleshooting.md`](../how-to/troubleshooting.md).
