# Capture Cleanup & Status — Implementation Plan (Plan A, interleaves 3/4/5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Each task below is independently committable — run `pytest -q` after every commit.

**Goal:** Close the three P0 bugs the user is feeling as "everything is stale," make the A/B/C curator triad legible in code, collapse duplicated plumbing, and introduce `lore status` as the single "is it alive?" surface. Leaves the capture/curator pipeline behaviorally identical on the happy path.

**Architecture:** No new capture behavior. This plan (a) fixes bugs, (b) renames `lore_curator/core.py` → `curator_c.py` so the code tells the triad story, (c) consolidates six scattered live-state queries behind one `CaptureState` model, and (d) adds `lore status` as the activity-first surface that renders `CaptureState`. `lore doctor` drops its activity panel and reverts to install-integrity-only with a footer pointer to `lore status`. SessionStart banner and `/lore:loaded` are rewired onto `CaptureState`. `lore runs list --hooks` stays a history view (row-per-event) and only picks up the shared iteration helper — it is *not* a CaptureState renderer.

**Tech Stack:** Python 3.11+, typer + rich (existing), `fcntl` (stdlib, POSIX — this plan is POSIX-only; Windows support is not a goal). No new runtime dependencies.

**Spec references:**
- `docs/superpowers/specs/2026-04-19-passive-capture-v1-design.md` — pipeline design (unchanged)
- `docs/superpowers/specs/2026-04-20-auto-session-diagnostics-design.md` — observability design (unchanged; this plan consolidates its renderers, no new signals)
- Memory: `project_curator_triad.md` (A/B/C roles), `project_lore_heartbeat.md` (SessionStart + time + global lock)

**Context (why this plan exists):**

Three-agent review (architect + code-reviewer + ui-ux-designer, 2026-04-20) surfaced the bugs and drift this plan addresses. A four-agent pass on the first draft (architect + code-reviewer + ui-ux + merciless-dev) sharpened task boundaries, lock primitives, and test strategy — findings folded below.

1. **P0 bugs.** `lore doctor` silently side-effects (spawns Curator B on calendar rollover via the hook probe at `doctor_cmd.py:83` → `hooks.py:1012-1031`). Curator A spawn throttle (`hooks.py:1135-1163`) is a TOCTOU: reads stamp → `Popen` → writes stamp, so concurrent sessions both spawn (root cause of #17). `WikiLedger.last_curator_a` is read by the banner but never written in prod — a permanent lie.
2. **Duplication.** Four relative-time formatters, two ancestor-walk impls, six copies of "list archival runs, skip `.trace.jsonl`," three live-state renderers reaching directly into files.
3. **"Curator" overloaded.** `lore_curator/core.py` (950 LOC frontmatter hygiene) is wired as the default `run_curator` even though the A/B/C triad is the intended model.
4. **No single activity surface.** `lore doctor` mixes install integrity with liveness. `/lore:loaded` is a static cache dump with no live view. `lore runs list --hooks` is opt-in. Nowhere answers "is lore doing anything for me right now?" in <10 lines.

**Non-blocker items to surface during execution** (carry forward, don't block ship):
1. `curator.log` plain-text file in `curator_b.py` has zero readers (confirmed by grep during review). Delete in Task 9a.
2. "Simple-tier fallback active" sentinel (`warnings.log` marker) shipped in v1 per UX — rendered as a one-line alert in `lore status` when present. Field is in `CaptureState`.
3. Team-mode / git-merge-induced mtime concerns carried from Plan 1 non-blockers still apply; unchanged here.
4. Plan 2.5 (`claude -p` subprocess backend, #16) is a hard blocker for Plans 3/4/5. This plan adds NO new LLM call sites (grep-verified during execution: zero new hits on `from anthropic` or `.messages.create(`).
5. **`doctor` shells the real hook as its probe.** Merciless-dev flag: this is tight coupling we work around in Task 1 via `--probe`, not fix. Future improvement: extract the hook's read-only banner/cache render into a callable function that `doctor` imports directly, and stop shelling out. Out of scope here; file as follow-up if the `--probe` flag accumulates other special cases.

**Phases:**
- **Phase 0 — P0 bugs** (Tasks 1–3): doctor side-effect, spawn throttle via `flock` (closes #17), lying breadcrumb.
- **Phase 1 — Triad legibility** (Tasks 4–5): rename `core.py` → `curator_c.py`, reframe module identity.
- **Phase 2 — Plumbing consolidation** (Tasks 6–9b): timefmt, ancestor-walk, run-log iteration, dead-code cull, pending-breadcrumb migration.
- **Phase 3 — `CaptureState` + `lore status`** (Tasks 10–13): new model, new command, rewire existing surfaces, `/lore:loaded` live section.

Each task is independently committable. TDD (red/green) per CLAUDE.md. Snapshot the exact current test count before starting (`pytest --collect-only -q | tail -1`) and assert it as a floor after each commit — no silent test deletions.

---

## Phase 0 — P0 Bugs

### Task 1: Stop `lore doctor` from spawning a curator as a side-effect

**Files:**
- Edit: `lib/lore_cli/hooks.py` (gate the calendar-rollover spawn behind non-probe invocations; make `--probe` suppress ALL spawn paths, not just Curator B, to future-proof)
- Edit: `lib/lore_cli/doctor_cmd.py` (pass `--probe` when invoking the hook)
- Test: `tests/test_doctor_no_side_effects.py`

**Problem:** `doctor_cmd.py:83` shells `python -m lore_cli hook session-start --plain` to confirm the hook is reachable. That hook path at `hooks.py:1012-1031` unconditionally spawns Curator B on calendar-day rollover. Running `lore doctor` to check on a stuck run *starts a new run*.

**Fix:** Add a hidden `--probe` flag on `lore hook session-start` that suppresses side effects: no curator spawns (A, B, or C), no stamp/lock file writes, no ledger mutations. Banner + cache rendering (read-only) stay intact. `doctor` invokes with `--probe`.

**Key signatures:**

```python
@hook_app.command("session-start")
def cmd_session_start(
    cwd: str = typer.Option(None, "--cwd"),
    plain: bool = typer.Option(False, "--plain"),
    probe: bool = typer.Option(False, "--probe", hidden=True,
                               help="Suppress all side-effects; used by lore doctor."),
) -> None:
    ...
    if not probe:
        # Existing curator-B calendar-rollover spawn block
        # Future: any other time-triggered spawns gated here too
        ...
```

**Acceptance:**
- `test_doctor_probe_writes_no_state_files` — snapshot `.lore/` directory contents (file list + mtimes + hashes) before `lore doctor`; invoke; assert byte-for-byte equal after. Catches any spawn / stamp / lock / ledger side effect.
- `test_doctor_does_not_spawn_curator_b` — monkeypatch `_spawn_detached_curator_b` to count calls; invoke `lore doctor`; assert 0 spawns even when `last_curator_b` is older than today.
- `test_doctor_does_not_spawn_curator_a` — same pattern (defense in depth; A is SessionEnd-triggered so this is regression-only today, but the probe contract covers it).
- `test_session_start_hook_spawns_without_probe` — baseline: without `--probe`, calendar-rollover still spawns (regression guard for normal path).
- `test_probe_flag_is_hidden_in_help` — `lore hook session-start --help` does not show `--probe`.

**Commit:** `fix(doctor): probe mode avoids all spawn side-effects`

---

### Task 2: `fcntl.flock` spawn throttle with process-exit auto-release (closes #17)

**Files:**
- Edit: `lib/lore_cli/hooks.py` (rewrite `_spawn_detached_curator_a` and `_spawn_detached_curator_b` as thin wrappers around a single throttle helper)
- Edit: `lib/lore_core/lockfile.py` (add `try_acquire_spawn_lock` using `fcntl.flock(LOCK_EX | LOCK_NB)`)
- Test: `tests/test_spawn_throttle_concurrent.py` (multi-process, not threads)

**Problem:** `hooks.py:1135-1163` reads `last-curator-a-spawn` → checks cooldown → `Popen` → writes stamp. Two concurrent SessionEnd hooks both read the stale stamp, both pass the cooldown gate, both `Popen`. No mutual exclusion.

**Fix (architect-reviewed, O_EXCL rejected in favor of flock):** Use `fcntl.flock(LOCK_EX | LOCK_NB)` on a long-lived `$LORE_ROOT/.lore/curator-<role>.spawn.lock` file. Two primitives, cleanly separated:
- **Mutual exclusion:** `flock` — auto-releases on process exit, so orphan-from-crashed-spawner recovery is free.
- **Cooldown:** a plain timestamp file written *inside* the critical section after a successful spawn. Read at lock-acquire to decide whether to proceed.

```python
# lockfile.py
import fcntl
from contextlib import contextmanager

@contextmanager
def try_acquire_spawn_lock(lore_root: Path, role: str):
    """Yields (lock_held: bool, stamp_path: Path). flock auto-releases on exit."""
    lock_path = lore_root / ".lore" / f"curator-{role}.spawn.lock"
    stamp_path = lore_root / ".lore" / f"curator-{role}.spawn.stamp"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.touch(exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        yield (False, stamp_path)
        return
    try:
        yield (True, stamp_path)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

# hooks.py
def _spawn_detached_curator_a(lore_root: Path, *, cooldown_s: int = 60) -> bool:
    with try_acquire_spawn_lock(lore_root, "a") as (held, stamp):
        if not held:
            return False
        if _stamp_within_cooldown(stamp, cooldown_s):
            return False
        # Migration: silently unlink the legacy last-curator-a-spawn stamp if present.
        _migrate_legacy_stamp(lore_root, "a")
        try:
            subprocess.Popen(cmd, start_new_session=True, ...)
        except (OSError, subprocess.SubprocessError):
            return False
        _write_stamp(stamp)
        return True
```

The existing `curator.lock` (mkdir-based, held while the curator runs) is unchanged — it serializes the *work*. `.spawn.lock` serializes the *decision to spawn*.

Delete `_curator_spawn_stamp`, `_cooldown_active`, `_write_spawn_stamp` in their current form; the new code lives alongside `try_acquire_spawn_lock`.

**Migration:** Old `last-curator-{a,b}-spawn` stamp files at `$LORE_ROOT/.lore/` are silently unlinked on first spawn attempt. If the migration fails (permissions), emit an `outcome=warning` event to `hook-events.jsonl` (observable, not silent) and continue — the new stamp + lock model doesn't depend on the old files.

**Acceptance:**
- `test_concurrent_spawns_only_one_wins_multiprocess` — use `multiprocessing.Process` (NOT threads — threads share FDs and can mask `flock` races) with `multiprocessing.Barrier(8)` so all 8 processes cross the gate within microseconds of each other. Assert exactly one reports a spawn; seven report contended. This is the #17 regression guard and the most important test in the plan.
- `test_stale_lock_reclaimed_after_spawner_crash` — use `multiprocessing.Process` that acquires the spawn lock then `os._exit(1)` without releasing; in the parent, immediately call `_spawn_detached_curator_a` and assert it proceeds (flock released on process exit). Proves orphan recovery is free.
- `test_cooldown_blocks_second_call_within_window` — sequential: two back-to-back calls, cooldown=60s, first spawns, second returns False with cooldown reason.
- `test_cooldown_expired_allows_spawn` — write stamp to mtime=(now - 2×cooldown); next call spawns.
- `test_spawn_failure_releases_lock` — mock `Popen` to raise; flock auto-released on `with` exit; next call within cooldown can acquire the lock (though stamp logic will still block if within cooldown — this test targets lock release specifically).
- `test_legacy_stamp_migration_unlinks_old_files` — pre-create `last-curator-a-spawn`; invoke spawn; assert file is gone. If unlink raises (permission), assert a warning is emitted to `hook-events.jsonl`.

**Commit:** `fix(curator): flock-based spawn throttle with process-exit orphan recovery; closes #17`

---

### Task 3: Write `last_curator_a` / `last_curator_b` / `last_curator_c` on run-end

**Files:**
- Edit: `lib/lore_curator/curator_a.py` (call `WikiLedger.update_last_curator("a")` at run-end; WRAP in try/except that logs to `hook-events.jsonl` but does not swallow silently)
- Edit: `lib/lore_curator/curator_b.py` (same for "b") — **FIRST verify current write path** via grep and dry-run; the previous review could not confirm whether `last_curator_b` is ever written. Record the finding in the commit message.
- Edit: `lib/lore_core/ledger.py` — two edit sites:
  - Parse side (around line 189-205 in `_load`/`read`): add `_dt(raw.get("last_curator_c"))`
  - Serialize side (around line 36-37 / write path): include `last_curator_c` in JSON output
  - Add `update_last_curator(role: Literal["a","b","c"]) -> None` method
- Test: `tests/test_wiki_ledger_last_curator.py` and extend existing curator tests

**Problem:** `WikiLedger.last_curator_a` is read by `breadcrumb.py:212-213` to render "last curator Nh ago" in the SessionStart banner, but `grep -r "last_curator_a =" lib/` returns nothing outside test fixtures. Banner has been rendering either never-set or stale-from-migration data.

**Fix:** Add `last_curator_c: datetime | None` to ledger schema (optional, back-compat for old ledgers). Add `WikiLedger.update_last_curator(role)` helper. Each curator calls it at run-end. Write failures are logged via `hook_log` (observable) but do not raise past the curator's top-level error handler — this is opportunistic telemetry, not a correctness path.

**Write-failure contract:**
- If `update_last_curator` raises: log a `warning` event to `hook-events.jsonl` with the exception type + message. Re-raise only if the curator was mid-`run-end` — otherwise swallow and continue. Never silently swallow.

**Acceptance:**
- `test_update_last_curator_a_persists_and_roundtrips`
- `test_curator_a_run_updates_last_curator_a` — integration: invoke Curator A; reread `WikiLedger`; assert `last_curator_a` is within the last few seconds.
- `test_curator_b_run_updates_last_curator_b` — same shape.
- `test_missing_last_curator_c_field_loads_as_none` — back-compat.
- `test_partial_failure_does_not_clobber_prior_last_curator_a` — seed ledger with `last_curator_a = <known timestamp>`; invoke Curator A but mock note-writing to raise mid-run; assert `last_curator_a` is EITHER the prior value OR the new value (NOT absent/None). The contract is "atomic or unchanged."
- `test_ledger_write_failure_emits_warning_event` — mock ledger write to raise; invoke Curator A; assert a `warning` event appears in `hook-events.jsonl` with exception details.
- `test_banner_renders_real_last_curator_time` — set `last_curator_a` to 30m ago; render banner; assert the "30m ago" substring appears.

**Commit:** `fix(ledger): write last_curator_{a,b,c} on run-end so banner stops lying`

---

## Phase 1 — Triad Legibility

### Task 4: Rename `lore_curator/core.py` → `curator_c.py`

**Files:**
- Rename: `lib/lore_curator/core.py` → `lib/lore_curator/curator_c.py` (single `git mv` commit)
- Edit: `lib/lore_curator/__init__.py` (re-exports swap from `core` to `curator_c`)
- Edit: `lib/lore_cli/__main__.py` (swap `from lore_curator import core as curator_cmd` → `from lore_curator import curator_c as curator_cmd`)
- Edit: `tests/test_curator_*.py` — update imports mechanically

**Goal:** Make the triad visible in the filesystem. Pure rename, zero behavioral changes.

**Acceptance:**
- Before starting, capture the exact current test count: `pytest --collect-only -q | tail -1` → record the number in the commit message body as "baseline: N tests." After the rename, assert the number is identical.
- `python -m pytest -q` — all tests pass unchanged (exact same count).
- `grep -r "from lore_curator import core" lib/ tests/` — zero hits.
- `grep -r "lore_curator.core" lib/ tests/` — zero hits.
- `git log --follow lib/lore_curator/curator_c.py` — shows full history from `core.py`.

**Commit:** `refactor(curator): rename lore_curator/core.py to curator_c.py`

---

### Task 5: Reframe Curator C's module identity and CLI help

**Files:**
- Edit: `lib/lore_curator/curator_c.py` (module docstring; rename `run_curator` → `run_curator_c` with a top-level alias `run_curator = run_curator_c` if any external import exists — grep first, add alias only if needed)
- Edit: `lib/lore_cli/__main__.py` — if there's a `lore curator run` subcommand help string to update, update it to mention A/B/C modes. **Confirm via grep BEFORE editing**: `grep -n "curator run" lib/lore_cli/__main__.py`. If no such string exists, skip this edit and note in the commit message.
- Test: `tests/test_curator_c_identity.py` (new; small)

**Goal:** Module docstring opens with "Curator C — weekly defrag / converge / stale-flag / supersession." CLI help (if present) reflects the triad.

**Scope cut from previous draft:** The docs/superpowers/specs/ grep is removed — specs aren't code, update them when next edited.

**Acceptance:**
- `test_curator_c_module_docstring_mentions_defrag_and_weekly` — string-in-docstring assertion.
- `lore curator run --help` mentions A/B/C modes clearly (IF the help string exists; skip this assertion if Task's grep found nothing).
- Existing `test_curator*.py` all pass.

**Commit:** `refactor(curator): reframe curator_c as the weekly-defrag member of the A/B/C triad`

---

## Phase 2 — Plumbing Consolidation

### Task 6: Single `lore_core/timefmt.py` — one `relative_time()`

**Files:**
- Create: `lib/lore_core/timefmt.py`
- Delete: `_relative_cap` (`doctor_cmd.py:374-392`), `_relative_time_cli` (`runs_cmd.py:239-255`), `_relative_time` + `_relative_time_short` (`breadcrumb.py:245-280`)
- Edit: each caller to import from `lore_core.timefmt`
- Test: `tests/test_timefmt.py`

**Key signatures:**

```python
def relative_time(
    ts: datetime | str | None,
    *,
    now: datetime | None = None,   # injectable for tests
    short: bool = False,
) -> str: ...
```

**Acceptance:**
- `test_relative_time_all_bucket_transitions` — table test: 0s, 59s, 60s, 3599s, 3600s, 86399s, 86400s → expected strings.
- `test_relative_time_handles_naive_datetime` — naive → assumed UTC.
- `test_relative_time_handles_z_suffix_iso_string`.
- `test_relative_time_none_returns_question_mark`.
- `test_relative_time_future_timestamp` — `relative_time(now + 5min)` returns `"just now"` (canonical choice for clock-skew robustness). Pin this in both the test and the module docstring.
- `test_relative_time_short_mode_omits_ago` — `short=True` → `"5m"` not `"5m ago"`.
- **Grep acceptance (strengthened):** `grep -rE "def (_)?(relative|rel)_(time|cap|ago)" lib/` returns only hits inside `timefmt.py`. Count of call-site imports of `relative_time` is ≥ 4 (previously the four duplicates).

**Commit:** `refactor(core): one relative_time() to replace four duplicates`

---

### Task 7: Single ancestor-walk for `## Lore` config

**Files:**
- Keep: `lib/lore_core/session.py::_walk_up_lore_config` as canonical; add `MAX_ANCESTOR_WALK = 20` as a module-level named constant
- Delete: `lib/lore_cli/hooks.py::_find_lore_config`
- Edit: `hooks.py` to import and use `_walk_up_lore_config`
- Test: `tests/test_session.py` extended

**Reconciliation:** `session.py` uses 12, `hooks.py` uses 20. Pick 20.

**Acceptance:**
- `test_walk_up_finds_lore_config_at_20_levels` — 20-deep temp tree, walk finds root CLAUDE.md.
- `test_max_ancestor_walk_is_20` — `from lore_core.session import MAX_ANCESTOR_WALK; assert MAX_ANCESTOR_WALK == 20` (drift guard).
- `grep -rn "_find_lore_config\|_walk_up_lore_config" lib/` returns only `session.py` definitions + import sites.
- Existing tests pass.

**Commit:** `refactor(core): unify ancestor walk for CLAUDE.md ## Lore resolution`

---

### Task 8a: Shared `iter_archival_runs` helper (add only, no call-site migration)

**Files:**
- Edit: `lib/lore_core/run_reader.py` — add `iter_archival_runs(lore_root: Path, *, limit: int | None = None) -> Iterator[Path]` yielding newest-first archival paths, filtering `.trace.jsonl`.
- Test: `tests/test_run_reader_iteration.py`

**Split rationale:** Code-reviewer flagged the combined "add helper + migrate 6 sites" as hidden refactor scope. Splitting lets 8a land alone; 8b migrates call sites one subsystem at a time.

**Acceptance:**
- `test_iter_archival_runs_skips_trace_companions`
- `test_iter_archival_runs_newest_first`
- `test_iter_archival_runs_honors_limit`
- `test_iter_archival_runs_empty_directory_yields_nothing`
- `test_iter_archival_runs_stable_order_on_same_mtime` — two files with identical mtime → sorted by stem alphabetically (reverse), deterministic.
- `test_iter_archival_runs_skips_partial_write` — zero-byte `<id>.jsonl` file → yielded (up to caller to handle), but never raises.

**Commit:** `feat(core): iter_archival_runs helper for shared run-log iteration`

---

### Task 8b: Migrate call sites to `iter_archival_runs`

**Files:**
- Edit: `lib/lore_cli/doctor_cmd.py:236-240` (capture-pipeline panel — will be removed entirely in Task 12a, so skip if 12a lands first; otherwise migrate now)
- Edit: `lib/lore_cli/runs_cmd.py:88-92` and `:190-194` (`list` and `list --hooks`)
- Edit: `lib/lore_cli/breadcrumb.py:99-102` (banner)
- Edit: `lib/lore_core/run_retention.py:37-41` (retention)

**Acceptance:**
- `grep -rE "glob\(.*jsonl.*\)" lib/` returns only the one call in `iter_archival_runs` (other call sites may still glob for different patterns — inspect each hit).
- Existing tests for each call-site pass unchanged (integration spot-check).

**Note:** JSONL-parse-with-recovery consolidation is NOT in this plan. If a natural boundary emerges during execution, file a follow-up issue; don't extend this task.

**Commit:** `refactor(cli): migrate four call sites to iter_archival_runs`

---

### Task 9a: Dead-code cull (unconditional deletions)

**Files:**
- Delete: `lib/lore_curator/curator_b.py::_curator_log` + the `curator.log` plain-text append (grep-verified zero readers during review).
- Delete: `lib/lore_curator/curator_a.py` imports of `WikiLedger` (line 13) and `NoteworthyResult` (line 21) — unused (code-reviewer finding).
- Probe: `_legacy_cache_path` fallback in `lib/lore_cli/hooks.py:56-59, 795-806` — **before deleting**, run `ls ~/.cache/lore/sessions/` on the developer machine and grep the cache layout. If zero legacy-format files, delete unconditionally. If any exist, file a follow-up issue and do NOT delete in this commit.
- Test: existing tests pass; add one grep-based acceptance for the unconditional deletions.

**Acceptance:**
- `grep -r "curator\.log" lib/` returns zero hits.
- `lib/lore_curator/curator_a.py` does not import `WikiLedger` or `NoteworthyResult`.
- `_legacy_cache_path` either deleted (probe found zero files) OR follow-up issue filed (probe found files) — commit message records which path was taken.

**Commit:** `refactor: cull dead code (curator.log, unused imports)`

---

### Task 9b: `pending-breadcrumb.txt` → `hook-events.jsonl` migration

**Files:**
- Edit: `lib/lore_cli/breadcrumb.py:18-80` — replace `write_pending_breadcrumb` file-write with an append to `hook-events.jsonl` as a `pending-breadcrumb` event; replace `consume_pending_breadcrumb` with a reader that queries the most recent `pending-breadcrumb` event from `hook-events.jsonl`.
- Edit: `lib/lore_core/hook_log.py` — add `pending-breadcrumb` as a valid event type (schema update, or use the existing generic-event pathway).
- Migration: on first SessionStart post-upgrade, if `$LORE_ROOT/.lore/pending-breadcrumb.txt` exists, read it, write its content as a `pending-breadcrumb` event to `hook-events.jsonl`, then `unlink` the file. One-shot, idempotent (second upgrade has no file to migrate).
- Test: `tests/test_pending_breadcrumb_migration.py`

**Split rationale:** Merciless-dev and architect both flagged this as a storage migration, not a cull. Gets its own task with an explicit migration path.

**Acceptance:**
- `test_pending_breadcrumb_round_trips_via_hook_events` — write a pending breadcrumb via the new API; read it back; assert content matches.
- `test_pending_breadcrumb_staleness_derived_from_event_ts` — `_PENDING_BREADCRUMB_MAX_AGE_S = 3600` still enforced, but derived from event `ts` instead of file mtime. Old behavior preserved.
- `test_legacy_pending_breadcrumb_txt_migrates_on_session_start` — pre-create `pending-breadcrumb.txt` with known content; invoke SessionStart hook; assert (a) event exists in `hook-events.jsonl` with that content, (b) file is unlinked.
- `test_legacy_migration_is_idempotent` — call migration twice; second call is a no-op.
- `grep -r "pending-breadcrumb" lib/` returns only hook-events code paths (no file I/O to `.txt`).

**Commit:** `refactor(hooks): fold pending-breadcrumb.txt into hook-events.jsonl with one-shot migration`

---

## Phase 3 — `CaptureState` + `lore status`

### Task 10: `CaptureState` model + query

**Files:**
- Create: `lib/lore_core/capture_state.py`
- Test: `tests/test_capture_state.py`

**Goal:** One frozen dataclass + one query function. Single source of truth for any "what's happening with lore right now?" renderer.

**Key signatures (revised per architect + merciless feedback):**

```python
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

@dataclass(frozen=True)
class CuratorStatus:
    role: str                        # "a" | "b" | "c"
    last_run_ts: datetime | None
    last_run_notes_new: int | None
    last_run_errors: int | None
    last_run_short_id: str | None    # consumed by `lore status` alert copy
    spawn_lock_held: bool
    work_lock_held: bool
    overdue: bool                    # a: >24h, b: calendar-day rollover, c: >7d

@dataclass(frozen=True)
class CaptureState:
    lore_root: Path
    scope_attached: bool
    scope_name: str | None           # e.g. "private/lore" (ADDED per architect)
    scope_root: Path | None          # CLAUDE.md parent directory (ADDED per architect)
    curators: list[CuratorStatus]    # length 3, ordered [a, b, c]
    last_note_filed: tuple[datetime, str] | None  # (ts, wikilink)
    pending_transcripts: int
    hook_errors_24h: int
    hook_log_failed_marker_age_s: int | None
    simple_tier_fallback_active: bool             # kept per UX (v1 ship)

def query_capture_state(lore_root: Path, *, cwd: Path | None = None) -> CaptureState: ...
```

**Changes from first draft:**
- Added `scope_name` and `scope_root` (architect).
- Kept `simple_tier_fallback_active` (UX ships v1).
- Kept `last_run_short_id` (UX uses it in alert copy; merciless wanted it cut but UX copy "`last 2 runs (abc123, def456) filed 0 notes`" requires it).

**Acceptance:**
- `test_capture_state_empty_vault` — fresh vault → all Nones / zeros, no exceptions.
- `test_capture_state_populated_vault` — fixture with 3 runs, 5 hook events (1 error), lock held → every field populated correctly.
- `test_capture_state_scope_resolution` — attached cwd → `scope_attached=True`, `scope_name` matches CLAUDE.md value, `scope_root` matches parent.
- `test_capture_state_unattached_cwd` — `/tmp` → `scope_attached=False`, `scope_name=None`, `scope_root=None`.
- `test_capture_state_overdue_calculation_per_role` — table test: A with `last_run_ts = now - 25h` → `overdue=True`; B with `last_run_ts.date() < today` → overdue; C with `>7d` → overdue.
- `test_capture_state_simple_tier_fallback_detected` — create `warnings.log` marker → field True; absent → False.
- `test_capture_state_query_is_readonly` — invoke query, assert mtime of every file in `.lore/` unchanged.
- `test_capture_state_query_is_fast` — <100ms on a vault with 200 runs and 10k hook events (perf guard).

**Commit:** `feat(core): CaptureState model + query — single source of liveness truth`

---

### Task 11: `lore status` command

**Files:**
- Create: `lib/lore_cli/status_cmd.py`
- Edit: `lib/lore_cli/__main__.py` to register the subcommand
- Test: `tests/test_status_cmd.py`

**Goal:** Activity-first default. No `--plumbing` flag (cut per merciless + UX; `lore doctor` already owns install integrity).

**Output shape (UX-reviewed ordering — decay-first):**

```
lore: active · private/lore · attached at ~/git/lore

  · Last note    [[2026-04-19-zarr-chunking]] · 18h ago
  · Last run     2h ago · 0 notes from 3 transcripts
  · Pending      2 transcripts (threshold 3)
  · Session      loaded 4m ago · 3 notes injected · /lore:loaded
  · Lock         free

  ! last 2 runs (abc123, def456) filed 0 notes — lore runs show abc123
```

**Per-line glyph rules:**
- `·` healthy (dim)
- `!` yellow threshold crossed
- `x` red threshold crossed

**Loud-on-earning thresholds (UX-revised):**
- Last note >3d → red (line glyph `x`); >24h → yellow (line glyph `!`). (Revised from >7d/>24h per UX: "a daily-use knowledge system with 7d red is too patient.")
- Last run `notes_new == 0` across ≥2 consecutive runs → yellow alert block at bottom with specific run IDs.
- Work lock held > 1h → yellow.
- Hook log failed marker present, age < 24h → red.
- Simple-tier fallback active → yellow one-line alert.

On a healthy system: 6 lines body (no alert block), first line shows scope attachment.

**Unattached-cwd message (UX-verbatim copy):**

```
lore: not attached here

  cwd ~/some/path is not inside a configured wiki.
  Run /lore:attach to bind this folder, or cd into an attached vault.
  (Configured vaults: private/lore at ~/git/lore)
```

**`--json` mode:** emits the raw `CaptureState` for scripting (no `--plumbing` flag).

**Key signatures:**

```python
@app.callback(invoke_without_command=True)
def status(
    cwd: str = typer.Option(None, "--cwd"),
    json_out: bool = typer.Option(False, "--json", help="Emit CaptureState as JSON."),
) -> None: ...
```

**Acceptance:**
- `test_status_happy_path_exactly_7_lines` — fixture vault with recent activity; invoke `lore status`; assert `output.count('\n') == 7` (first line + blank + 5 body lines). Pin exact count; not substring.
- `test_status_happy_path_no_alert_block` — same fixture; assert no `!` or `x` glyphs present.
- `test_status_alerts_appear_on_repeated_zero_notes` — fixture with 2 recent 0-notes runs; assert `!` alert line appears with both run IDs.
- `test_status_stale_note_yellow_at_25h` / `test_status_stale_note_red_at_72h_plus_1s` — table test for threshold transitions (1h / 25h / 72h+1s).
- `test_status_stale_lock_yellow_at_61m`.
- `test_status_hook_log_failed_red`.
- `test_status_simple_tier_fallback_yellow`.
- `test_status_json_mode_is_structured` — `--json` output parseable; contains `scope_name`, `curators`, `last_note_filed`.
- `test_status_unattached_cwd_verbatim_copy` — message exactly matches UX-approved copy; includes "Configured vaults:" enumeration.
- `test_status_line_order_is_decay_first` — assert Last note appears before Last run appears before Pending appears before Session appears before Lock in the output (bytes-wise).

**Commit:** `feat(cli): lore status — activity-first liveness surface`

---

### Task 12a: Drop `lore doctor` activity panel; add footer pointer

**Files:**
- Edit: `lib/lore_cli/doctor_cmd.py` — remove `run_capture_panel` invocation and the function itself (or keep as internal if still used elsewhere — grep). Keep ✓/✗ install checks. Add a single footer line after the checks:

```
Install looks good. For activity: lore status
```

(On failure, replace with: `Some checks failed — see above. For activity: lore status`.)

- Test: update `tests/test_doctor.py` to match new output.

**Rationale (UX):** `lore doctor` = install integrity only. `lore status` = activity. Two-purpose `doctor` was confusing.

**Acceptance:**
- `test_doctor_no_capture_pipeline_panel` — invoke; assert "Capture pipeline" substring is NOT present.
- `test_doctor_footer_points_to_status` — assert "For activity: lore status" is present.
- `test_doctor_install_checks_unchanged` — existing ✓/✗ per-check assertions hold.

**Commit:** `refactor(doctor): drop activity panel, point to lore status`

---

### Task 12b: SessionStart banner renders from `CaptureState`

**Files:**
- Edit: `lib/lore_cli/breadcrumb.py::render_banner` — use `query_capture_state()` for "X pending · last curator Y ago · Z errors." Delete duplicated query functions (`_most_recent_run_end`, `_recent_hook_errors`).
- Test: `tests/test_breadcrumb.py` extended.

**Acceptance:**
- `test_banner_renders_from_capture_state` — monkeypatch `query_capture_state` to return a known fixture; assert banner text contains expected substrings.
- `grep -rn "_most_recent_run_end\|_recent_hook_errors" lib/` returns zero hits (functions deleted).
- `grep -r "hook-events.jsonl\|runs/.*\.jsonl" lib/lore_cli/breadcrumb.py` returns zero direct reads.

**Commit:** `refactor(cli): banner renders from CaptureState`

---

### Task 12c: `lore runs list` migrates to Task 8 helper (no CaptureState)

**Files:**
- Edit: `lib/lore_cli/runs_cmd.py` — use `iter_archival_runs` from Task 8a for archival-list iteration. `--hooks` interleave keeps its own hook-event read (history granularity; CaptureState is for summaries, not history).

**Decision (architect):** `lore runs list --hooks` stays a history view, NOT a CaptureState renderer. `lore status` owns the summary niche.

**Acceptance:**
- `test_runs_list_uses_iter_archival_runs` — monkeypatch `iter_archival_runs` to yield a known fixture; assert `lore runs list` output matches.
- Existing `test_runs_cmd.py` passes unchanged.

**Commit:** `refactor(cli): lore runs list uses shared archival-iter helper`

---

### Task 13: `/lore:loaded` skill grows a live-state section (live-first)

**Files:**
- Edit: `skills/loaded/SKILL.md` (or wherever the Claude Code plugin skill lives — confirm via `find /home/buchbend/git/lore -name 'SKILL.md' -path '*loaded*'`)
- Edit: `lib/lore_mcp/server.py` if `/lore:loaded` routes through MCP (check during execution)
- Test: `tests/test_lore_loaded_live_section.py`

**Goal:** `/lore:loaded` currently dumps the SessionStart cache. It should ALSO show current state (`CaptureState` rendered via `lore status` logic), with live FIRST (per UX: "what's true now is the headline; cache is historical context").

**Output shape:**

```
── Live state (as of now) ────
<lore status output body, no header>

── Injected at SessionStart ────
[existing cache block]
```

**Acceptance:**
- `test_lore_loaded_contains_live_then_cache` — both sections present, live first (bytes-wise).
- `test_lore_loaded_live_section_updates_across_calls` — two invocations pick up a new run filed between them.
- `test_lore_loaded_renders_unattached_correctly` — invoked from unattached cwd: live section shows the Task 11 unattached-cwd copy; cache section empty or absent.
- `test_lore_loaded_handles_capture_state_query_failure` — mock `query_capture_state` to raise; assert live section degrades to a one-line error (`(capture state unavailable: <message>)`) and the cache section still renders.

**Commit:** `feat(skill): /lore:loaded shows live CaptureState (live-first, cache below)`

---

## Discoverability (must-have, bundled into Task 11's commit or a follow-up)

Per UX review:
- `lore --help` top-line entry: `status   Is lore doing anything for me right now?` (added in `__main__.py` when registering the subcommand — small edit)
- `lore doctor` footer pointer (Task 12a)
- `/lore:loaded` cross-reference (Task 13)

Nice-to-have: README quick-start update — defer to a docs-pass PR.

---

## Execution notes

- **Order:** Phase 0 must complete first — Tasks 1–3 fix active bugs and should ship standalone if desired. Phases 1–3 build on each other but each task is independently committable.
- **Test budget:** Expect ~35 new tests across the plan. Run `pytest -q` after every commit — the existing test count (snapshot at Task 4) must never decrease.
- **Worktree hygiene:** Per handover, after any worktree activity run `pip install -e /home/buchbend/git/lore` from the main repo.
- **Plan 2.5 check:** Grep for `from anthropic` and `.messages.create(` after Phase 2 — zero new hits expected.
- **After merge:** `last_curator_a/b/c` fields and `CaptureState` are the foundation Plan 5 (Curator C ship-dark) will build on.

## Success criteria

1. `lore doctor` does not spawn any curator (Task 1; snapshot test).
2. Concurrent SessionEnd hooks produce exactly one spawned curator under `multiprocessing.Barrier(8)` stress (Task 2; closes #17).
3. SessionStart banner shows real `last_curator_*` times, not stale/missing (Task 3).
4. `lib/lore_curator/` filesystem reflects the A/B/C triad (Tasks 4–5).
5. `grep -rE "def (_)?(relative|rel)_(time|cap|ago)"` returns only `timefmt.py` (Task 6); same-strictness greps for ancestor-walk and run-iter (Tasks 7–8).
6. Dead code removed; `pending-breadcrumb.txt` migrated with idempotent one-shot (Tasks 9a, 9b).
7. `lore status` renders 7-line output on happy path; loud alerts only when earned; unattached-cwd copy is verbatim per UX (Task 11).
8. Every liveness surface renders from `CaptureState` except `lore runs list --hooks` which is a history view (Tasks 10, 12a, 12b, 13).
9. Full test suite passes at or above the Task 4 snapshot count — no behavioral regression outside the intended fixes.
10. Disk/CPU profile unchanged on the hook hot-path.

## Review-pass log

This plan was drafted, then reviewed by four parallel agents (senior-architect, code-reviewer, ui-ux-designer, merciless-dev general-purpose) on 2026-04-20. Must-fix findings folded:

- **Architect:** Lock primitive changed from `O_EXCL` to `fcntl.flock` (auto-release on process exit). Test changed from threads to `multiprocessing`. `CaptureState` gained `scope_name`/`scope_root`. Task 9b (pending-breadcrumb migration) explicitly split with one-shot migration. `lore runs list --hooks` decision pinned (history view, no CaptureState).
- **Code-reviewer:** Multi-process + `Barrier(8)` concurrency test. Future-timestamp test in timefmt. Partial-failure test for Task 3. Test count pinned (not `524+`). Sharper greps. Tasks 8 and 12 split into sub-tasks.
- **UX:** `lore status` line order changed to decay-first (Last note → Last run → Pending → Session → Lock). Glyph prefixes added. `--plumbing` flag dropped. Thresholds tightened (>3d red instead of >7d). Unattached-cwd copy verbatim. `/lore:loaded` section order flipped (live first). `doctor` drops activity panel entirely (not "still has one, rewired"). Simple-tier fallback ships v1.
- **Merciless-dev:** Task 9 split (9a cull, 9b migration). `--plumbing` dropped from Task 11. Task 5 docs edit dropped. "Confirm during execution" language removed from Tasks 5, 8, 12. Explicit "doctor shells real hook" non-blocker #5 added.
