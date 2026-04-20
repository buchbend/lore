# Auto Session Writer Diagnostics — Design

- **Status:** draft
- **Date:** 2026-04-20
- **Project:** lore (CLI-first engineering memory)
- **Scope:** Observability layer for the passive-capture pipeline (debugging, awareness, live observation).
- **Spec version:** 1
- **Related:** `2026-04-19-passive-capture-v1-design.md` (pipeline design)

---

## Context

### Why

The passive-capture pipeline (Plans 1+2, shipped) runs in a hook hot-path and a detached background process. Both are effectively invisible. The only signal the user gets today is a single SessionStart banner:

```
lore: 2 pending · last curator 30m ago
```

That tells the user *something pending exists* and *a curator ran at some time*. It does not answer any of the real questions:

- Did my last session produce a note? If not, why not?
- Is the hook firing cleanly, or silently failing?
- If I tune the noteworthy filter, what changes?

Several failure modes are also invisible by design: hook exceptions are swallowed, noteworthy-filter drops advance the ledger with no artefact, lock contention is silent, and the `redaction.log` / `warnings.log` files grow unread. The user can inspect `.lore/transcript-ledger.json` manually, but the JSON is not designed for human reading.

This spec adds a thin observability layer that makes the pipeline inspectable without changing its behavior.

### User priorities (confirmed during brainstorming)

1. **Now — debugging.** When things go wrong, the user needs to answer *why*. Hook errors, silent drops, config tuning.
2. **Long-term — awareness.** Everyday "did it run, and what did it do?" as a passive signal.
3. **Nice-to-have — confidence.** Live observation of the background process to build trust in the system.

Phase 1 (this spec) covers debugging in full detail. Phases 2 and 3 get concrete sketches but ship in follow-up plans.

### Design constraints

- **No behavioral changes to the pipeline itself.** The observability layer is additive: Curator writes to the run log *in addition to* its current outputs. The existing passive-capture guarantees (idempotency, lockfile, threshold behavior) are untouched.
- **Observability must not break capture.** All log writes are best-effort (try/except). If the log is unwritable, the pipeline continues; the failure is surfaced later via `lore doctor`.
- **Hook hot-path stays <100 ms.** Hook-event log write is a single append to a small JSONL file — microseconds.
- **User-facing copy stays generic.** Commands and output say "curator run" / "last curator", not "Curator A". The A/B/C naming lives in code only (per user preference).

---

## Architecture overview

```
┌─────────────────────┐     ┌──────────────────────┐
│ Claude Code / Cursor│     │ Manual invocation    │
│ (hook events)       │     │ (lore curator run    │
│                     │     │  --dry-run)          │
└──────────┬──────────┘     └───────────┬──────────┘
           │                            │
           ▼                            ▼
    ┌─────────────┐             ┌──────────────┐
    │ capture()   │             │ run_curator_a│
    │ hot path    │───spawn────▶│ (background) │
    └──────┬──────┘             └───────┬──────┘
           │                            │
           │ emits                      │ emits via RunLogger
           ▼                            ▼
    ┌──────────────────────┐    ┌───────────────────────┐
    │ hook-events.jsonl    │    │ runs/<id>.jsonl       │
    │ (one line per event) │    │ (decision trace)      │
    │                      │    │ runs/<id>.verbose.jsonl│
    │                      │    │ (opt-in LLM I/O)      │
    └──────────┬───────────┘    └───────────┬───────────┘
               │                            │
               └────────────┬───────────────┘
                            │
                            ▼ read by
               ┌──────────────────────────────────┐
               │ lore runs  (history/debugging)   │
               │ lore doctor (health snapshot)    │
               │ lore curator run --dry-run       │
               │ SessionStart banner (discovery)  │
               └──────────────────────────────────┘
```

### Three pillars, one foundation

1. **Foundation — structured logs.** Two append-only JSONL streams under `$LORE_ROOT/.lore/`.
2. **Pillar 1 — debugging.** `lore runs {list, show, tail}`, `lore curator run --dry-run`, hook error capture with try/except.
3. **Pillar 2 — awareness.** `lore doctor` capture-pipeline panel, SessionStart banner surfacing of run errors and hook errors. SessionEnd breadcrumb sketched for Phase 2.
4. **Pillar 3 — live observation.** `lore runs tail` with simple polling in Phase 1; live TUI deferred.

---

## Data model

Two log streams, both JSONL, both under `$LORE_ROOT/.lore/`.

### Stream A: `hook-events.jsonl`

One line per hook invocation. Written synchronously by the wrapped `capture()` before it returns. Tiny (~300 bytes per line).

```json
{
  "schema_version": 1,
  "ts": "2026-04-20T14:32:05.421Z",
  "event": "session-end",
  "host": "saiyajin",
  "transcript_id": "claude-01HXYZ...",
  "scope": {"wiki": "private", "scope": "lore"},
  "duration_ms": 47,
  "outcome": "spawned-curator",
  "pending_after": 3,
  "run_id": "2026-04-20T14-32-05-a1b2",
  "error": null
}
```

**`event`** enum: `session-start`, `pre-compact`, `session-end`.

**`outcome`** enum: `ledger-advanced`, `below-threshold`, `spawned-curator`, `unattached`, `error`, `stale-lock`, `no-new-turns`.

`run_id` is populated when `outcome=spawned-curator`, linking into stream B. `error` carries `{type, message}` when `outcome=error`.

### Stream B: `runs/<run-id>.jsonl`

One file per Curator invocation. Filename is the run ID: `<ISO-timestamp>-<4-char-random-suffix>.jsonl`, e.g. `2026-04-20T14-32-05-a1b2.jsonl`.

Lines are decision records in chronological order. First line is `type=run-start`, last is `type=run-end`; everything between is per-transcript decisions.

Record types:

| `type` | Emitted | Key fields |
|---|---|---|
| `run-start` | Once at start | `run_id`, `trigger` (`hook`\|`manual`\|`dry-run`), `pending_count`, `config` (snapshot of relevant knobs: `noteworthy_tier`, `threshold_pending`), `ledger_snapshot_hash` |
| `transcript-start` | Once per transcript | `transcript_id`, `hash_before`, `new_turns` |
| `redaction` | When hits detected | `transcript_id`, `hits`, `kinds` (list) |
| `noteworthy` | After LLM filter | `transcript_id`, `verdict` (bool), `reason` (string), `tier`, `latency_ms` |
| `merge-check` | When considering merge | `transcript_id`, `target` (wikilink), `similarity`, `decision` (`merge`\|`new`) |
| `session-note` | On write | `transcript_id`, `action` (`filed`\|`merged`), `path`, `wikilink` |
| `skip` | When no note produced | `transcript_id`, `reason` (`noteworthy-false`\|`lock-held`\|`unattached`\|`no-new-turns`) |
| `warning` | Non-fatal issue | `message`, `context` |
| `error` | Caught exception | `transcript_id` (optional), `exception`, `message`, `traceback` |
| `run-end` | Once at end | `duration_ms`, `notes_new`, `notes_merged`, `skipped`, `errors`, `dry_run`, `log_write_failures` |

Every record carries `type`, `schema_version` (always `1` in v1), and `ts`.

### Verbose companion: `runs/<run-id>.verbose.jsonl`

Written only when `LORE_VERBOSE_CAPTURE=1` env var is set, or `--verbose-capture` flag on manual runs. Contains the same records as the main file plus two additional types at the same chronological points:

| `type` | Key fields |
|---|---|
| `llm-prompt` | `call` (`noteworthy`\|`merge-check`), `tier`, `token_count`, `messages` (full body) |
| `llm-response` | `call`, `token_count`, `body` (full response text) |

Verbose capture never happens by default. The file is separate from the main JSONL so the default path stays lean, and verbose retention is independently controlled.

### Writer layer

- **`lib/lore_core/run_log.py`** — `RunLogger` context manager. Opened at run-start, closed at run-end (including on exception). Every decision point in Curator A calls `logger.emit(type, **fields)`. Log writes are wrapped in try/except that swallows OSError and increments `_write_failures`.
- **`lib/lore_core/hook_log.py`** — `HookEventLogger`. Simpler: single-record append with rotation check on `hook-events.jsonl` size. I/O-free construction (no file handle until first emit).

### Modified sites

- `lib/lore_cli/hooks.py:capture()` — wrap in try/except; call `HookEventLogger` before return.
- `lib/lore_curator/curator_a.py:run_curator_a()` — construct `RunLogger` at entry; thread through to `session_filer`, `noteworthy.classify_slice`, merge-check callsite.

### Schema versioning

`schema_version: 1` on every record. Readers check and refuse unknown versions with a clear error message (not silent skip). No migrations in v1; schema evolves via version bumps later.

---

## Command surface

Three entry points. Each does one thing well.

### `lore runs` — execution history

```
lore runs list [--limit N] [--hooks] [--json]
lore runs show <run-id> [--verbose] [--raw] [--json]
lore runs tail [--follow]
```

**`lore runs list`** — default shows the last 20 curator runs as a Rich table:

```
ID                   Started    Duration  Transcripts  Notes              Errors
2026-04-20T14-32-05  2h ago     8.3s      3            1 new, 1 merged    0
2026-04-20T11-04-21  5h ago     4.1s      1            1 new              0
2026-04-20T09-15-33  7h ago     12.8s     2            0 (noteworthy=false)  1
```

- `--limit N` — override default of 20
- `--hooks` — interleave hook events by timestamp (dimmed rows); lets the user see the full timeline including "hook fired → below-threshold → nothing" gaps
- `--json` — passthrough of raw JSONL (no reformatting, no colors)

**`lore runs show <run-id>`** — layered output (summary panel + flat chronological log). ID accepts a full run ID or a unique prefix.

- `--verbose` — include LLM prompt/response records from the `.verbose.jsonl` companion. If the companion file doesn't exist, prints: *"Verbose capture not enabled for this run. Re-run with `LORE_VERBOSE_CAPTURE=1 lore curator run --dry-run` to capture."*
- `--raw` (only valid with `--verbose`) — disable 3-line truncation on LLM prompts/responses
- `--json` — passthrough

**`lore runs tail`** — streams the most recent run's log. If a run is active, follows it live. If no run is active, prints the last completed run and exits. `--follow` waits for the next run to start. Implementation: poll `stat().st_size` every 200 ms on the target file (no inotify dependency). Exits on `run-end` record unless `--follow`.

### `lore curator run --dry-run` — preview

```
lore curator run --dry-run [--verbose-capture]
```

- Triggers the full pipeline against current pending state
- Real LLM calls (noteworthy, merge-check) — real cost, real behavior
- **Writes nothing:** no session notes, no ledger advance, no lockfile acquisition
- **Bypasses threshold check** — always runs; prints "nothing to do" cleanly if pending=0
- **Bypasses lockfile** — a real run in progress does not block dry-run
- Output: flat log rendered to stdout in real time (same format as `lore runs show`)
- Also writes a run file with `trigger=dry-run` and `dry_run: true` on run-end, so dry-runs appear in history (clearly marked)
- `--verbose-capture` also writes the `.verbose.jsonl` companion

### `lore doctor` extension

Existing `lore doctor` gains **one new panel**:

```
Capture pipeline
  ✓ Last hook fired 12m ago (session-start, outcome: below-threshold)
  ✓ Last curator run 2h ago (8.3s, 3 transcripts, 0 errors)
  ✓ No stale lockfile
  ✗ 2 hook errors in last 24h — lore runs list --hooks
```

Single panel, read-only queries over the two log streams. Additive — empty state prints "No capture activity yet". Errors surface as warnings, not failures of `doctor` itself. Every failure line points at the exact command that shows more.

### Command → scenario mapping

| Scenario | Primary | Secondary |
|---|---|---|
| A — "no note appeared" | `lore runs show <latest>` | `lore runs list --hooks` |
| B — "plumbing feels off" | `lore doctor` | `lore runs list --hooks` |
| C — "tuning config" | `lore curator run --dry-run [--verbose-capture]` | `lore runs show --verbose` |

---

## Output format

### `lore runs show <id>` — layered

**Zone 1: Summary panel.**

```
╭─ Run 2026-04-20T14-32-05-a1b2 ────────────────────────────╮
│ Started   2026-04-20 14:32:05 UTC (2h ago)                │
│ Duration  8.3s                                            │
│ Trigger   hook (session-end)                              │
│ Outcome   1 new, 1 merged, 1 skipped · 0 errors           │
│ Notes     [[2026-04-20-zarr-chunking-decision]]           │
│           [[2026-04-19-auth-refactor]] (merged)           │
╰────────────────────────────────────────────────────────────╯
```

Dry-run header: `Run 2026-04-20T14-32-05-a1b2 (dry-run — no writes)` in yellow.
If `errors > 0`: the `Outcome` line is red.

**Zone 2: Flat decision log.**

Format: `HH:MM:SS ICON kind<padded>  message`. Monospace alignment. Icons:

| Icon | Kind | Color |
|---|---|---|
| `▶` | transcript-start | cyan |
| `·` | redaction, merge-check, noteworthy-true | dim / default |
| `⊘` | noteworthy-false, skip | yellow |
| `✓` | session-note (filed, merged) | green |
| `!` | warning | yellow |
| `✗` | error | red |
| `?` | unknown record type | magenta |
| `■` | run-end | default |

Example:

```
14:32:05 ▶ start          transcript claude-01 (hash abc1..def4, 47 new turns)
14:32:05 · redacted       2 hits (api_key, token)
14:32:06 · noteworthy     true — "substantive decision about Zarr chunking" (842ms)
14:32:07 ✓ filed          [[2026-04-20-zarr-chunking-decision]]
14:32:07 ▶ start          transcript claude-02 (hash xyz7..abc0, 12 new turns)
14:32:08 ⊘ noteworthy     false — "brief context check, no decisions" (621ms)
14:32:08 ⊘ skipped        noteworthy=false, ledger advanced
14:32:08 ▶ start          transcript cursor-01 (hash def3..ghi6, 89 new turns)
14:32:10 · noteworthy     true — "refactor discussion" (1.1s)
14:32:11 · merge-check    [[2026-04-19-auth-refactor]] similarity=0.84 → merge
14:32:11 ✓ merged         into [[2026-04-19-auth-refactor]]
14:32:11 ■ end            8.3s · 1 new, 1 merged, 1 skipped · 0 errors
```

Wikilinks render literally (`[[...]]`). No OSC 8 hyperlinks.

Long `reason` fields (>80 chars) truncate with ellipsis in the default view; full text remains in the JSONL and in `--verbose` output.

**Verbose mode** adds `llm-prompt` and `llm-response` records inline at their chronological position, indented with `┃`, truncated to 3 lines unless `--raw`:

```
14:32:06 · llm-prompt     noteworthy (tier=middle, 1247 tokens)
          ┃ System: You are a session-filter for a personal knowledge vault...
          ┃ User: <transcript slice>
          ┃ (truncated — 1180 lines; run with --raw for full)
14:32:06 · llm-response   noteworthy (verdict=true, 89 tokens)
          ┃ {"noteworthy": true, "reason": "substantive decision..."}
```

### `lore runs list --hooks` — interleaved

Hook-event rows render in a dimmer style, with outcome in place of notes-counts:

```
ID / Event                      Started    Duration  Summary
2026-04-20T14-32-05 (run)       2h ago     8.3s      1 new, 1 merged, 1 skipped · 0 errors
2026-04-20T14-32-05 (hook)      2h ago     47ms      session-end · spawned-curator
2026-04-20T14-28-11 (hook)      2h ago     31ms      session-start · below-threshold
2026-04-20T11-04-21 (run)       5h ago     4.1s      1 new · 0 errors
2026-04-20T11-04-21 (hook)      5h ago     52ms      session-end · spawned-curator
```

### `--json` mode

Strict passthrough of raw JSONL on both `list` and `show`. No reformatting, no added fields, no colors. This is the Phase 2 extensibility hook without a separate API.

---

## Banner & discoverability

### SessionStart banner — Phase 1 additions

Two new signals on top of the existing banner logic:

**1. Last-run error surfacing.** If the most recent curator run ended with `errors > 0`:

```
lore!: last run had 2 errors (5m ago) · lore runs show <id>
```

Uses the existing `lore!:` error prefix. `<id>` is the actual short run ID so it can be pasted directly.

**2. Hook-error surfacing.** If any hook in the last 24h had `outcome=error`:

```
lore: up to date · 47 notes in private/lore · 1 hook error today (lore doctor)
```

No `!:` prefix — hook errors rarely mean total breakage. Non-blocking trailing segment.

**Intentionally not surfaced in the banner:**
- Noteworthy=false decisions (noisy; visible in `lore runs show`)
- Unattached-cwd skips (expected behavior outside wiki-attached repos)
- Simple-tier fallback warnings (surfaced once via `lore doctor`, not banner)

### SessionEnd breadcrumb — sketched for Phase 2

Not shipped in Phase 1. Sketched here because it shapes the design's coherence:

```
lore: capture queued · curator spawned (pending 3)
lore: capture queued · below threshold (pending 2/3)
lore: capture skipped · unattached
```

Trivial to implement once hook events are structured — essentially an echo of the hook-event record. Follow-up plan.

### Documentation

One README section under `## Observability` naming the three commands with one-line descriptions and linking to a single detail section. No plugin slash-command additions in v1; revisit in Phase 2 if CLI proves clumsy.

---

## Retention & rotation

### Defaults

- **`hook-events.jsonl`** — rotate at 10 MB. Rotated file becomes `.1`. Keep one rotation (`.1` only; no `.2`). At ~300 B/event this is ~33k events — many months of normal use.
- **`runs/*.jsonl`** — keep last **200** runs. FIFO delete on overflow.
- **`runs/*.verbose.jsonl`** — keep last **30** verbose companions. Stricter cap because files are larger (tens of KB) and only written on opt-in.

### When cleanup runs

**Lazy cleanup at end of each Curator run.** After `run-end` is written, `RunLogger` does a single `glob + sort + unlink` pass to enforce caps. Costs milliseconds.

**Rotation** on `hook-events.jsonl` is checked in the hot-path before append (one `stat()` call, rotate only on threshold crossing, `rename()` is atomic).

No background daemon, no cron, no cleanup at hook hot-path time beyond the single stat.

### Config

Added to per-wiki config (`$LORE_ROOT/wiki/{name}/.lore-wiki.yml`):

```yaml
observability:
  hook_events:
    max_size_mb: 10
    keep_rotations: 1
  runs:
    keep: 200
    keep_verbose: 30
```

Omitting the section uses defaults. Extends `lib/lore_core/wiki_config.py` with one new dataclass (`ObservabilityConfig`) on `WikiConfig`.

### Explicitly out of scope

- Compression of rotated files
- Time-based retention
- Shipping logs off-host

---

## Failure modes & error handling

### Hook hot-path

Wrap `capture()`:

```python
def capture(event: str) -> None:
    start = time.monotonic()
    logger = HookEventLogger(lore_root)  # I/O-free
    try:
        # existing work...
        logger.emit(event=event, outcome=outcome, duration_ms=..., run_id=run_id)
    except Exception as exc:
        logger.emit(event=event, outcome="error",
                    duration_ms=int((time.monotonic()-start)*1000),
                    error={"type": type(exc).__name__, "message": str(exc)})
        raise
```

Errors logged AND re-raised. Claude Code's exit-code handling stays intact. If the logger itself throws during `emit()`, the exception is caught inside `emit()` (never raised), a process-local counter increments, and the failure surfaces in `lore doctor` on next run.

### Curator run

`RunLogger` is a context manager. If Curator A raises inside `with`:

1. `__exit__` emits a final `type=error` record with exception info
2. Then a `run-end` with `errors: N` incremented
3. File closed
4. Exception propagates up

Per-transcript errors caught at the loop level: emit `type=error` with `transcript_id`, continue to next transcript.

Lockfile release in existing `finally` — unchanged.

### Log-write failures

Every log write wrapped:

```python
def emit(self, **record):
    try:
        with self.path.open("a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        self._write_failures += 1
        # Never raise.
```

If `_write_failures > 0` at run-end, the final `run-end` record carries `log_write_failures: N`. Next `lore doctor` surfaces: *"Observability log writes failed recently — check disk space / permissions on $LORE_ROOT/.lore/"*.

### Malformed log handling (reader side)

- Unknown record `type` → render with `?` marker ("unknown record type 'foo' — schema mismatch?"). Do not crash.
- Malformed JSON line → `✗` marker ("malformed line, skipped").
- Missing required fields → render what's present; mark missing fields as `<missing>`.
- Schema version mismatch → refuse to render with clear error: *"Run written by newer lore (schema v2). Upgrade CLI to read."* Not silent skip.

### Dry-run concurrency

Dry-run explicitly bypasses lockfile and threshold check. If a real run is concurrently writing a session note while dry-run is computing noteworthy on the same transcript, the dry-run is computed against a snapshot of the ledger at dry-run start. The snapshot hash is recorded in `run-start.ledger_snapshot_hash` so divergent output can be debugged.

If a dry-run itself raises, the run file still gets a `run-end` record with `dry_run: true` and the error. Inspectable like any other run.

### Silent-failure audit — before and after

| Failure today | New visibility |
|---|---|
| Hook exception swallowed | `hook-events.jsonl` outcome=error; SessionStart banner "hook error today"; `lore doctor` panel |
| Noteworthy=false drop | `runs/<id>.jsonl` `type=noteworthy verdict=false` with reason; `lore runs show` renders `⊘` |
| Unattached cwd | `hook-events.jsonl` outcome=unattached (informational only; intentionally not alerted) |
| Lock contention | `hook-events.jsonl` outcome=stale-lock OR run-log `type=skip reason=lock-held` |
| Simple-tier fallback | Already in `warnings.log`; `lore doctor` surfaces once per tier change |
| Redaction hits | `redaction.log` + per-run `type=redaction` record; rendered as dim `·` in `lore runs show` |

The only silent-failure mode kept silent is `unattached` — expected behavior outside a wiki-attached repo.

---

## Testing strategy

### Unit tests

**`RunLogger` (`lib/lore_core/run_log.py`)**
- Records emitted in order; file closed cleanly; `run-end` always written
- Exception inside `with`: error record + run-end emitted, exception propagates
- Log-write failure (mock `open` to raise OSError): `_write_failures` incremented, pipeline continues, `run-end` carries `log_write_failures`
- Schema version stamped on every record
- Dry-run mode: `trigger: dry-run` in run-start, file still written

**`HookEventLogger` (`lib/lore_core/hook_log.py`)**
- Append-only write with correct schema
- Rotation: crosses `max_size_mb` threshold → rename to `.1`, continue on fresh file
- Error outcome captures exception type and message
- I/O-free construction (no file handle until first emit)

**`runs_cmd.py` — command layer**
- `lore runs list` empty state
- `lore runs show <prefix>` resolves ambiguous prefix with clear error
- `lore runs show --verbose` without companion file prints explicit message
- `--json` mode byte-level passthrough
- Malformed record rendered with `✗` marker, no crash

**`wiki_config.py` — config parsing**
- `observability` section absent → defaults applied
- Partial section → only specified fields override defaults
- Invalid types → clear validation error

### Integration tests

Happy-path end-to-end:

1. Set up wiki with two pending transcripts
2. Invoke `capture()` with `event=session-end`, threshold=1
3. Assert `hook-events.jsonl` has one row, `outcome=spawned-curator`
4. Wait for Curator A to finish (sync test-mode flag)
5. Assert `runs/<id>.jsonl` exists with expected record sequence
6. Assert session note written
7. Invoke `lore runs list` → run appears
8. Invoke `lore runs show <id>` → output contains expected wikilinks and counts

Dry-run:

1. Same setup, pending state
2. Invoke `lore curator run --dry-run`
3. Assert no session note, no ledger advance, no lockfile
4. Assert `runs/<id>.jsonl` exists with `trigger: dry-run` and `dry_run: true`

Retention:

1. Create 205 synthetic run files
2. Invoke a real run
3. Assert oldest 5 deleted; new run file present

Rotation:

1. Write a 9.9 MB `hook-events.jsonl`
2. Invoke a hook
3. Assert `.1` exists with old content; fresh file has the new event

### Renderer tests

Flat-log renderer is pure: `(records: list[dict]) -> str`. No I/O mocks. Snapshot tests per record type.

### TDD expectations

Per user's global CLAUDE.md (red/green TDD, YAGNI, DRY), implementation drives each component test-first. `RunLogger` and `HookEventLogger` are written first — foundation everything else depends on.

### Out of scope for tests

- Claude Code hook invocation machinery (Claude Code's responsibility)
- LLM verdict content (test emission, not the verdict itself)
- Terminal rendering ANSI codes (Rich handles; snapshot the structured output)

---

## Out of scope / deferred

Sketched in Section 5 / Section 3 but shipped in follow-up plans:

- **SessionEnd breadcrumb** — echoes the hook-event outcome at session close
- **Live TUI dashboard** (`lore watch`) — Rich/Textual app with auto-refresh
- **Slash commands** (`/lore:runs` etc.) — CLI-first in v1; revisit if CLI proves clumsy
- **Replay / `--from-run` dry-run modes** — YAGNI for now; trigger-now is enough
- **Compression and time-based retention** — count-based is simpler and covers the use cases
- **Off-host log shipping** — unrelated to observability; Curator C "central" mode is the right place for it
- **`lore runs prune` manual cleanup command** — lazy cleanup is adequate; add only if users ask

---

## Migration notes

None. This is additive:

- New files: `run_log.py`, `hook_log.py`, `runs_cmd.py`
- Modified files: `hooks.py` (wrap `capture()` in try/except, add logger call), `curator_a.py` (thread `RunLogger` through), `doctor_cmd.py` (add panel), `wiki_config.py` (add `ObservabilityConfig`), `breadcrumb.py` (extend banner)
- New config section: optional `observability:` block (defaults apply if absent)
- No schema changes to existing ledgers, session notes, or wiki config structure
- Existing installations pick up the new behavior on first capture after upgrade; first hook event and first curator run create the new files

---

## Success criteria

Phase 1 is done when:

1. Every silent-failure mode from the audit table has a path to user visibility (hook errors, run errors, log-write failures, malformed records)
2. User can answer Scenario A ("no note appeared") with a single `lore runs show <latest>` invocation
3. User can answer Scenario B ("plumbing feels off") with `lore doctor`
4. User can answer Scenario C ("tuning config") with `lore curator run --dry-run --verbose-capture` + `lore runs show --verbose`
5. Hook hot-path stays <100 ms even with observability writes
6. All unit and integration tests pass
7. Disk use bounded by retention config; no runaway growth
