# Lore Code-Quality Review (post-v0.10.2)

**Reviewer:** code-reviewer agent
**Scope:** extends `docs/REVIEW-2026-04-25-three-lens-state-of-lore.md` and the `docs/REVIEW-2026-04-26-claim-audit.md`. Phases 0–8 closed most of the original audit, so the focus here is on what those reviews missed and what looks already-fixed.

---

## Top 5 issues

### 1. `no_anthropic_client` skip-reason still leaks vendor naming through telemetry — HIGH
- `lib/lore_curator/session_curator.py:385` and `lib/lore_curator/daily_curator.py:83-84` still emit `skip_reason="no_anthropic_client"` (and `"no-anthropic-client"` for the run-log). The whole point of Phase 0's `LlmClient` rename was to stop spelling "anthropic" in user-facing telemetry; this is the only surface where the old vocabulary still shows up.
- Knock-on: `tests/test_curator_a.py:553-575`, `test_curator_b.py:488-500`, `test_mvp_capture_subprocess_e2e.py:328-329` all assert on the legacy string, locking it in. Not flagged by the prior reviews.
- **Fix:** rename the constant to `no_llm_client`; update call sites + tests in one pass. Add to the drift guard.

### 2. The "legacy cache" deprecation path is *actively populated*, not a fallback — MEDIUM
- `lib/lore_cli/hooks.py:997` writes to `_legacy_cache_path()` on **every** SessionStart. The deprecation comment at `hooks.py:84-99` claims the legacy file is "read-only fallback" that's safe to delete once enough time passes; in fact every new session keeps recreating it, so the file's mtime never ages out. The "deprecation will fire when no environment has produced legacy state for a while" trigger is unreachable.
- **Fix:** stop writing the legacy path (delete `hooks.py:996-999`); keep the read-only fallback at `_context_log()` for one release; then delete both. Audit found by checking the matching write to the deprecation comment — neither prior review caught this.

### 3. Side-effecting registration of defrag passes via import — MEDIUM
- `lib/lore_curator/c_orphan_links.py:285`, `c_adjacent_merge.py:286`, `c_auto_supersede.py:321` all call `_register()` at module top level, mutating `defrag_curator._DEFRAG_PASSES`. The `_ensure_passes_registered()` function (`defrag_curator.py:634`) admits this is a circular-import workaround. Consequences:
  - Pass-registration order depends on import order. Two test sessions importing in different orders see different pass orderings.
  - `if pass_fn not in _DEFRAG_PASSES` guards against duplicates — O(N) on each registration; harmless today but a code smell.
  - Any test that does `importlib.reload(defrag_curator)` resets the registry but doesn't reset the imported pass modules → silently empty defrag.
- **Fix:** explicit registry passed into `_run_defrag_passes`, populated by `_ensure_passes_registered()` at call time from a hard-coded list. Ten lines of refactor; eliminates the import-order coupling.

### 4. `run_curator_a` in `session_curator.py:162-205` has duplicated dry-run / non-dry-run loop bodies — MEDIUM
- The two branches differ only in `dry_run=True/False` and the lockfile guard. ~20 lines of identical logic (pending iteration, `_process_entry` call, outcome recording, `touched_wikis` accumulation) appear twice. A future bug fix touching one branch will divergently miss the other — exactly the failure mode the Phase 6 `run_curator_c` decomposition was meant to prevent.
- **Fix:** extract a `_iterate_pending(...)` helper used by both branches; the lock context manager wraps the call site, not the loop body.
- The 2026-04-25 review flagged `run_curator_c` as the 180-line god function; it didn't catch the same shape in `run_curator_a`.

### 5. `import logging` is dead in `lib/lore_curator/daily_curator.py:4` — LOW (canary for cleanup discipline)
- The module imports `logging` and never references it (only the string "logging" appears in a comment at line 405). Trivial, but the prior reviews ran careful broad-except + dead-code audits and missed this — suggests a quick `ruff --select F401` sweep across `lib/` would surface other small dead imports cheaply.

---

## Dead / deprecated paths to delete

- `lib/lore_cli/hooks.py:996-999` — legacy cache write path (see issue 2). Once removed, also delete `_legacy_cache_path` (`hooks.py:84`) and the read-fallback at `_context_log` (`hooks.py:894-905`). The deprecation marker is honest about the intent; the writer just contradicts it.
- `lib/lore_cli/breadcrumb.py:75` calls `migrate_legacy_pending_breadcrumb()` unconditionally on every breadcrumb load. Same pattern: the migration is documented as one-shot but runs perpetually. Either guard it with a "migration done" sentinel file or remove now that v0.10.x users have all converged.
- `lib/lore_curator/curator_c_diff.py` — module name still uses the old curator-c label. Phase 8 explicitly chose to keep `c_*.py` and `curator_c_diff.py` prefixes for grouping, but the user-facing log file path `$LORE_ROOT/.lore/curator-c.diff.YYYY-MM-DD.log` is now visible to ops dashboards using the deprecated name. Either accept it (rename to `defrag-diff.YYYY-MM-DD.log` with a one-release alias) or document it as the canonical wire format.
- `lib/lore_core/lint.py:368` — `"status": n.status, # legacy — retained during deprecation`. No removal target; been "during deprecation" through eight phases.
- `lib/lore_core/schema.py:24` — same comment, no removal version. Either pin to a target 0.x release or drop the noun "deprecation" until it's really being deprecated.
- `lib/lore_curator/daily_curator.py:4` — unused `import logging` (issue 5).
- The `extract_open_items` v1→v2 session-note migration block (`defrag_curator.py:50-150`) is reachable only via the interactive `lore curator … --migrate-open-items` flow; if the v1 corpus is fully converted internally, this block plus its CLI surface can drop.

---

## Test coverage gaps

- **No end-to-end SessionStart → curator-A → MCP integration test.** `test_mvp_capture_subprocess_e2e.py` exercises hook → ledger → curator → session note via subprocess (good), but never queries the result through the MCP `lore_search` / `lore_read` path. The "did the freshly-filed note become reachable to Claude?" loop is the project's value proposition; today it's covered only by individually mocked unit tests on each side. Risk: catalog/index drift between curator-write and MCP-read goes silently uncaught.
- **`_DEFRAG_PASSES` registration is not unit-tested.** `_ensure_passes_registered` and the `_register()` side effects have no direct test; integration tests pick them up incidentally. If issue 3 is refactored, regression coverage starts at zero.
- **No test for `_maybe_reindex` cache invalidation under concurrent mtime updates.** `tests/test_mcp_reindex_throttle.py` covers the time-based throttle, but does not verify that a user editing a note mid-throttle (mtime changes inside the 5s window) eventually sees fresh search results once the window expires. Risk: stale-search bug pattern is exactly what would surface here.
- **`handle_surface_context` (`lore_mcp/server.py:296-308`) returns a non-`_mcp_error`-shaped error envelope** (`{"schema": ..., "wiki": ..., "error": "string"}`). Phase 5 migrated 8 handlers; this one was missed. There is no test asserting envelope-shape *uniformity* across all `handle_*` returns — the existing `test_mcp_error_envelope.py` only covers the migrated ones, leaving freshly-written handlers free to drift again. Add a parametric test that introspects every `handle_*` and confirms error returns match the schema.
- **Cross-platform `os.kill(pid, 0)` semantics on Windows.** `tests/test_hooks_pid_alive.py` covers POSIX errno paths but the host fleet is documented as supporting macOS + Linux only; if Windows ever lands, the `_pid_alive` contract needs validation.
- **`run_curator_a` dry-run vs non-dry-run parity is not asserted** (issue 4). A test that runs both modes against an identical fixture and asserts that the outcomes-list is identical (modulo writes) would catch divergent bugs before they ship.
- **`scaffold_wiki()` in `new_wiki_cmd.py` is the canonical scaffolder** but `tests/test_cli_wiki.py` (4 tests) only exercises the `lore wiki new` alias path — not the `lore new-wiki` legacy form. Risk is asymmetric: the alias works because the canonical form works; the *converse* check (alias still works once the canonical changes) is what the legacy-shim contract needs.

---

## Notable strengths

- **The Phase 0–8 audit-and-execute pattern is exemplary.** The roadmap doc tracks every claim from the original review with a status (DONE / DEFERRED / DEBUNKED), pointing at the exact phase that addressed it. The willingness to debunk three of the original review's claims by source-reading is unusual and disciplined.
- **`tests/test_layering.py` is real architecture-as-code.** Static-grep enforcement of "no `from lore_cli` in `lore_core/`" survives even lazy-imports inside functions. This kind of guard is the right answer to "how do we make sure the architectural fence stays up after the refactor lands?"
- **`require_lore_root()` + typed exceptions (`lib/lore_core/config.py`)** is exactly the shape this codebase needed. The two-resolver pattern (silent-default vs strict-raise) plus the dataclass-style `LoreRootMissing(path)` makes CLI error handling clean and uniform.
- **`atomic_write_text` + flock-guarded append discipline** is consistent across cache, ledger, hook-events, and diff-log writers. The fact that the prior review's "concurrent-write corruption" claim was *debunked* by reading the existing implementation is a strong signal.
- **Function-aliasing for the curator A/B/C → role-name rename** (`run_session_curator = run_curator_a` etc.) is the right call. The 188 call sites stay valid; new code uses the new name; both forms test-cover the same code path. Pragmatic refactoring.
- **The `_mcp_error` envelope helper** with a code/message/next-hint shape is the right contract; the docstring even calls out which other layer (JSON-RPC) intentionally uses a different envelope. Once `handle_surface_context` is migrated (test gap above), the contract is clean.

---

## Items the prior reviews flagged that look already-fixed or stale

- **"Two parallel SessionStart hooks"** — debunked correctly in the claim audit; both are intentional and have distinct responsibilities. No further action.
- **"`_pid_alive` Linux-only"** — fixed (Phase 3); cross-platform via `os.kill(pid, 0)` with all errno paths tested. Done.
- **"86 broad excepts"** — partially landed. Current count in `lib/`: 61 occurrences of `except Exception`. The `# noqa: BLE001` comments now mark intentional defensive wraps, which is the right discipline; raw broad-excepts without comments are the ones still worth chasing.
- **"`run_curator_c` 180-line god function"** — fixed (Phase 6); decomposed to 145 lines + three helpers.
- **"`BODY_TEMPLATE` writes `- TODO`"** — fixed (Phase 6); now writes `_Fill in_`.
- **"Three names for scope"** — debunked in claim audit; `_scopes.yml` (catalog), `scopes.json` (registry), `attachments.json` (consent) have distinct roles. Documented in `docs/architecture/state.md`.

---

## Synthesis hint for downstream agents

The codebase is in materially good shape post-Phase-8. The remaining issues fall into two buckets:

1. **Telemetry hygiene** (issues 1–2): vendor naming and dead-code paths that look fixed in docs but are alive in code. These are the kind of bugs that pile up when "deprecation" comments are written but the writer side isn't audited together.
2. **Structural smells in code that *was* refactored** (issues 3–4): `_DEFRAG_PASSES` registration and `run_curator_a`'s duplicated branches. The Phase 6 decomposition pattern (`_filter_already_ran_this_week`, etc.) should be applied here next.

Test-coverage gap that matters most: the curator → MCP integration loop. Everything else is incremental.
