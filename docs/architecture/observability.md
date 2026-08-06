# Observability: one spine, three commands

**Audience:** contributors debugging why a note didn't appear, adding a
new spine producer, or wondering why `lore log`/`lore news`/`lore runs`/
`lore proc` no longer exist as commands.

Before PRD 0005's epic, debugging one background flush meant walking
seven uncorrelated surfaces — `lore log`, `lore proc`, `lore runs`,
`lore status`, `lore news`, `lore doctor`, and a crash-log directory —
over three different ad-hoc file schemas, with no identifier tying a
hook fire to the curator run it spawned to the note it published.
Failure handling leaned on "availability over debuggability" past the
point of usefulness: drain writes never raised, mid-session flush
failures deferred silently, retention cleanup swallowed its own
errors. The full problem statement lives in
[PRD 0005](../prd/0005-onboarding-config-observability.md).

The replacement is one append-only event log (the **spine**) that every
background producer writes to, a **trace_id** field correlating records
into one story, and **three** read commands instead of seven. Issue
#361 later retired the compose pipeline these mechanisms correlated —
the flush state machine and the trace_id's one minter went with it (see
below) — but the spine, `lore status`, `lore trace` and `lore doctor`
stayed, now correlating the surviving producers: hooks, the hygiene
curator, transcript sync, the retention janitor, and flags.

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
  `hook`, `curator`, `drain`, `janitor`, `install`, `mcp`, `flag`. A
  source outside this set is a bug, not data — `validate_envelope()`
  rejects it.
- **`level`** — `info` / `warn` / `error`.
- **`error_code`** — `None`, or a value from the closed `ErrorCode`
  `StrEnum`. Free-form detail (exception type, message, offending path)
  belongs in `data`, never in `error_code` directly.
- Fields a producer can't yet know (`trace_id` when nothing has minted
  one, `run_id` before a run starts, …) are written as explicit `null`,
  never omitted — readers never have to guess "unknown" from "field
  dropped".

**Producers today:**

| `source` | Module(s) | What it emits |
|---|---|---|
| `hook` | `lore_cli/hooks.py`, `lore_core/spine.py:emit_hook_event` | SessionStart/PreCompact/Stop/UserPromptSubmit/SessionEnd/Capture hook fires and their outcomes |
| `curator` | `lore_core/run_log.py`, `lore_curator/hygiene.py` | `run-start` / `run-end` and per-action decision records for a `lore curator` hygiene run (role `c`) — the only curator that still runs |
| `drain` | `lore_core/drain.py` | `transcript-synced` — the one event kind with a live producer; read by `lore status`'s news section |
| `janitor` | `lore_core/janitor.py`, `lore_core/run_retention.py`, `lore_cli/_crash_log.py` | Retention deletions/downgrades and their failures |
| `install` | *(reserved, no producer yet)* | Onboarding-wizard events (PRD 0005 pillar C); not wired as of this writing — `lore init` doesn't emit to the spine today |
| `mcp` | `lore_core/ledger.py` | `ledger-query` — one per `lore_drill` call whose query names an issue, a PR, an epic or a file path; a query that names none of those returns without emitting |
| `flag` | `lore_core/flag.py` | `flag-write` (outcome `written`/`withheld`) and `flag-review` (verdict `accept`/`decline`/`retarget`), each carrying `flag_id` — read by `lore status`'s flags section and `lore trace flag` (`lore_core/flag_metrics.py`) |

A drain event kind or a spine `ErrorCode` value is added only together
with the code that produces it — a kind or code with no raiser is a
reader surface with no writer, and this epic's guard test
(`tests/test_producerless_surfaces_gone.py`) fails the build on one.

**Schema-version policy:** `SCHEMA_VERSION` (currently `2`) bumps on any
change to the envelope *shape* — a field added, removed, renamed, or a
semantic change to an existing field. Adding an `ErrorCode` value is
additive and does **not** bump the version; removing one narrows what a
reader may see and does. Readers must skip an unfamiliar `v`, never
crash on it.

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

## trace_id — a correlation field with no current minter

**Defined by:** `lore_core/spine.py:new_trace_id()` (an opaque
`secrets.token_hex(8)`). The compose pipeline was its only caller —
`lore_curator/chapter_flush.py:spawn_detached_flush` minted one per
flush and threaded it through `LORE_TRACE_ID`, the curator's run
events, the matching drain events, and a published note's
`linkage.trace_id` frontmatter field. Issue #361 deleted
`chapter_flush.py` with the rest of the compose pipeline, so nothing
mints a fresh `trace_id` today, and nothing stamps `linkage.trace_id`
onto a note a flag appends to.

`lore trace <selector>` (below) still reads every spine record sharing
a `trace_id`, sorted by timestamp — there is no separate correlation
index, the trace *is* the filtered spine — so the mechanism stays
correct for any future producer that threads one through. Until one
exists, a `trace-id` or note-path/`[[wikilink]]` selector only resolves
against pre-retirement data; `session-id` and `flag` are the selectors
with live data behind them today (see "`lore trace <selector>`"
below).

---

## The flush lifecycle state machine — retired

**Module:** `lore_core/flush_store.py` (issue #189, retired by #377).
`FlushRecord` used to track one flush unit (keyed by buffer stem)
through:

```
queued -> running -> published | withheld | dead-lettered(reason)
```

Issue #361 deleted the compose pipeline that drove this machine; issue
#377 deleted the write half that survived it — `begin`, `transition`,
`record_failure`, the legal-transition table, and the bounded-retry
backoff are gone. `FlushStore` is a reader now: `list()` reads whatever
records are still on disk, and `purge()` is the retention janitor's
entry point (below) — no code opens a new record. A `queued` or
`running` record on disk today is a pre-#361 leftover, not a flush in
flight; see
[the troubleshooting guide](../how-to/troubleshooting.md#a-queued-or-running-flush-record-on-disk).

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
healthy right now?" dashboard — capture liveness (last hygiene run,
last hook fire), per-wiki connection health (dirty / ahead / behind /
reachable), a **flags** section (per-wiki flags written, withheld,
pending, accepted, declined, retargeted — `lore_core.flag_metrics`
aggregating the `source="flag"` spine events `flag.py` emits, plus
`flag.count_pending` for the pending count itself), retention usage, an
absorbed **news** section (background drain events — the old
`lore news`), and an alerts section where every warning names its exact
drill-down command. Reads only; exit code is 0 when healthy, nonzero when
any alert fires. Every row reflects a state some code still writes —
issue #377 removed the four capture rows and the flushes panel the
compose pipeline used to feed.

### `lore trace <selector>`

**Module:** `lore_cli/trace_cmd.py`, business logic in
`lore_core/trace.py`. Renders the chronological, correlated story of one
trace_id, as a Rich tree (or `--plain` aligned text, or `--json` raw
JSONL). Absorbs the debugging role of the old `lore log` / `lore runs` /
`lore proc`. The selector accepts a trace_id (see above — no current
producer mints one), a session_id (resolves every trace_id that session
touched, newest first — the selector with live data behind it today),
`flag` (lists every flag-write/flag-review spine event as a flat,
chronological table instead of a tree — a flag carries no trace_id, it
is a standing-alone fact; pairing one flag's write and verdict lines by
`flag_id` gives its review latency), or a note path / `[[wikilink]]`
(reverse-resolved through the note's own `linkage.trace_id` — unset on
any note a flag appended to, since no current writer stamps it). The
`dead` and `last` selectors are gone with the flush lifecycle record
they resolved through; either now raises the same "no trace, session,
or note matches" error as any other unknown selector.

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

### Removed: `log`, `news`, `runs`, `proc`, `drain`

Each was kept as a thin deprecated alias for one minor-version window,
then removed outright once that window closed (see the CHANGELOG for
the exact removal version) — none of the five is a `lore` command
today. `lore log` / `lore runs` / `lore proc` are fully absorbed by
`lore trace`; `lore news` by `lore status`'s news section. `lore drain`
(and its sole subcommand, `prune`) had no replacement to alias to — it
pruned a file (`.lore/drain/_system.jsonl`) that has had no writer
since drain events moved onto the spine, so its CLI surface was removed
outright; `lore_core.janitor.prune_orphans` still exists and runs
automatically as part of every opportunistic janitor pass.

---

## See also

- [PRD 0005](../prd/0005-onboarding-config-observability.md) — the full
  problem statement and every implementation decision.
- [`state.md`](state.md) — concurrency guarantees shared with the spine,
  and the three state files this doc doesn't cover (`_scopes.yml`,
  `scopes.json`, `attachments.json`).
- [`cli-contract.md`](cli-contract.md) — the shape every `lore <verb>`
  command follows, including these three and the deprecating-a-verb
  pattern the now-removed aliases used.
- How-to: [`../how-to/onboarding.md`](../how-to/onboarding.md),
  [`../how-to/troubleshooting.md`](../how-to/troubleshooting.md),
  [`../how-to/measure-flag-quality.md`](../how-to/measure-flag-quality.md)
  — the known-gem baseline and directive flip-probe that use the flags
  section and `lore trace flag`.
