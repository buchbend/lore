# Lore Cleanup Roadmap

**Origin:** `docs/REVIEW-2026-04-25-three-lens-state-of-lore.md` (three-lens audit).
**Working principle:** one phase per session. Plan only the *next* phase in detail; later phases stay sketched until they become next. Tick the checkbox and fill the **Session log** when a phase lands.

> **How to use this file at session start**
> 1. Find the first ☐ phase below.
> 2. Read its **Goal**, **Scope**, and **Definition of done**.
> 3. If it's still a sketch (phases 2-7 today), open a planning sub-session first to flesh it out *here*, then execute.
> 4. End of session: tick the box, write the **Session log**, and write a one-line scope-refinement for the next phase if the work surfaced anything that should change it.

---

## Status board

- ☑ **Phase 0** — Stop the bleeding *(2026-04-25)*
- ☑ **Phase 1** — Layering fence (`lore_cli` decomposition) *(2026-04-25)*
- ☐ **Phase 2** — Config consolidation
- ☐ **Phase 3** — `hooks.py` decomposition
- ☐ **Phase 4** — Naming + concept consolidation
- ☐ **Phase 5** — UX polish
- ☐ **Phase 6** — Test hygiene + curator decomposition
- ☐ **Phase 7** — Performance + scaling prep *(optional)*

**Milestone 1** = Phase 0 + Phase 1 (stop bleeding + erect the fence). Re-evaluate phasing of 2-7 after Milestone 1 — the fence will likely re-shape them.

---

## Phase 0 — Stop the bleeding

**Status:** ☑ completed 2026-04-25
**Estimated session length:** 1-2 sittings
**Why first:** these are actively biting *now* (per user's own memory + the review).

### Goal
Land the highest-ROI fixes that are causing silent breakage today, before any structural work.

### Scope
- **Version sync** — make `pyproject.toml`, `.claude-plugin/plugin.json`, and `CHANGELOG.md` agree. Add a CI guard that fails if they disagree.
- **`anthropic_client` → `llm_client` rename** — across all 48 sites in `lore_curator/`. Route every `.messages.create(...)` call through `LlmClient`. Add a smoke test that runs at least one defrag pass on the OpenAI backend.
- **Slash command honesty** — either ship `skills/on/SKILL.md` + `skills/loud/SKILL.md` (mirror the off/quiet inverses), or collapse the toggles so `/lore:off` re-toggles. Pick one.
- **Dead-code purge** — delete `lib/lore_core/migration/` (empty), `build/lib/lore_import/` (stale wheel artifact), unused `LORE_ROOT`/`WIKI_ROOT` module constants in `config.py:30`. Add `make clean` or `.gitignore` entry to keep `build/` out.

### Definition of done
- All three version sources agree, CI fails if they don't.
- `grep -rn "anthropic_client" lib/` returns zero matches in `lore_curator/` (or only inside `LlmClient`'s Anthropic backend).
- Smoke test runs defrag with `LORE_LLM_BACKEND=openai` and passes.
- `/lore:` autocomplete in Claude Code matches what's documented.
- `lib/lore_core/migration/` and `build/lib/lore_import/` are gone.

### Out of scope
- No `hooks.py` refactor (that's Phase 3).
- No layering changes (that's Phase 1).
- No naming changes beyond `anthropic_client` → `llm_client`.

### Session log

**2026-04-25** — single sitting, all four scoped items landed plus
opportunistic cleanup of `AnthropicClientProtocol` (a redundant Protocol
class hand-rolled in `noteworthy.py` that pre-dated `LlmClient`).

**Landed**
- Dead code: `build/` and `lib/lore_core/migration/` removed (both
  gitignored, neither tracked); module-level `LORE_ROOT`/`WIKI_ROOT`
  constants and their re-export in `lore_core/__init__.py` deleted —
  no callers found via grep.
- Versions: `.claude-plugin/plugin.json` 0.5.0 → 0.9.0;
  `CHANGELOG.md` gained a `## [0.9.0]` entry honestly noting the
  0.4.x–0.8.2 backfill gap; `tests/test_version_sync.py` enforces all
  three sources via pytest (the project's de-facto CI). Pointer added
  in `CONTRIBUTING.md` so future contributors know about the guard.
- Skills: `skills/on/SKILL.md` and `skills/loud/SKILL.md` shipped as
  inverses of `off`/`quiet`. `/lore:` autocomplete now matches what
  the docs advertise.
- Rename: `anthropic_client` → `llm_client` across 200 occurrences in
  28 files (10 lib, 18 tests). `AnthropicClientProtocol` removed in
  favour of importing `LlmClient` from `lore_curator.llm_client`.
- Smoke test: `tests/test_curator_openai_smoke.py` exercises
  `make_llm_client(backend="openai")` → `classify_slice` end-to-end
  with a mocked OpenAI SDK; proves the curator path now reaches the
  OpenAI backend cleanly. 1434 → 1435 tests passing.

**Punted (out of Phase 0 scope but surfaced)**
- The `/lore:off` / `/lore:quiet` *sentinel mechanism* itself is not
  actually wired up. SKILL.md describes writing
  `$TMPDIR/lore-off-<session>` and hook code checking for it — but
  `grep -rn "lore-off" lib/` finds nothing in the hook code, only the
  doc string. Phase 0 fixed the user-visible slash-command surface
  area; the underlying mute behavior is a separate bug. **Open as a
  GitHub issue under "UX honesty: mute toggles don't actually mute."**
- `tests/test_hooks_v2.py::test_session_start_from_lore_happy_path`
  is failing pre-existing — asserts `"lore: active"` but the new
  status line (commit 88cc783) is `"lore 0.X.Y: active"`. Pure test
  rot, pre-existing, unrelated to Phase 0 changes. **One-line fix
  for next maintenance pass.**
- Six test files still have `FakeAnthropic*` class names
  (`test_curator_a.py`, `test_curator_b.py`, `test_curator_b_briefing_integration.py`,
  `test_auto_diagnostics_e2e.py`, `test_graph_abstraction_e2e.py`,
  `test_mvp_capture_e2e.py`). Test-internal cosmetic; not user-facing.
  Leave for a future cosmetic sweep.
- The CHANGELOG gap (versions 0.4.0 — 0.8.2 missing entries) is
  acknowledged in the 0.9.0 stub; backfill is a separate docs task.
- `.gitignore` already excludes `build/`; no Makefile or `make clean`
  target was added because nothing was tracked-and-needs-cleaning.

**Surprised**
- The grumpy-dev claim of "OpenAI backend silently no-op or broken on
  every defrag pass" was *almost* right but not quite — the
  parameter was lying about its type, but the call shape
  (`client.messages.create(..., tools=[...], tool_choice={...})`)
  already worked through `OpenAICompatibleClient`'s translation layer.
  The rename is honest-now-where-it-was-lying; the smoke test confirms
  end-to-end behavior. Less of a "broken" fix and more of a
  "stop-lying-about-what-this-parameter-accepts" fix.
- `CHANGELOG.md` had drifted *much* further than `plugin.json` — last
  entry 0.3.0 vs. plugin's 0.5.0. The README and CHANGELOG hadn't kept
  pace with `pyproject.toml` for ~6 versions.

**Scope refinements for Phase 1**
- The architect's pick still stands: `lore_cli` decomposition is the
  load-bearing structural fix.
- Now that `lore_curator/` is internally consistent on `LlmClient`,
  the "stop importing `lore_cli._compat` from curator/core/mcp"
  refactor in Phase 1 has one fewer naming axis to reconcile.
- The pre-existing `test_hooks_v2` failure should be fixed alongside
  Phase 1's hook touches (it's a one-line assertion update).

---

## Phase 1 — Layering fence (`lore_cli` decomposition)

**Status:** ☑ completed 2026-04-25
**Estimated session length:** 2-4 sittings (this is the load-bearing phase)
**Why second:** the architect's strategic pick — every later phase becomes a local refactor once this fence is up.

### Goal
Restore a one-way dependency graph: `plugin/skills → lore_cli → lore_runtime → lore_core / lore_curator / lore_mcp / lore_search`. Stop lower layers from importing `lore_cli`.

### Scope
- Create a new `lib/lore_runtime/` package.
- Move `lore_cli/_compat.py` (`argv_main`) and `lore_cli/run_render.py` icons/render helpers into `lore_runtime` (or `lore_core` if they're truly deterministic).
- Update the four upward-importers to import from `lore_runtime` instead of `lore_cli._compat`:
  - `lib/lore_core/lint.py:706`
  - `lib/lore_core/migrate.py:15`
  - `lib/lore_curator/curator_c.py:988`
  - `lib/lore_mcp/server.py:826`
- Stop registering `curator_c.app` and `mcp_cmd.app` as nested typer apps in `lore_cli/__main__.py:48`. Expose them as library entrypoints; have `lore_cli` thinly dispatch.
- Add an import-time guard: `lore_core`, `lore_curator`, `lore_mcp` test modules assert no `lore_cli` import in their package's transitive imports.

### Definition of done
- `python -c "import lore_core; import lore_curator; import lore_mcp"` works without `lore_cli` having been imported (verifiable via `sys.modules`).
- A unit test enforces this — fails the build if anyone re-introduces an upward import.
- Existing CLI commands still work (smoke test: `lore --help`, `lore status`, `lore lint`, a curator dry-run).
- The MCP server can be started without typer being on the import path of `lore_mcp` modules (typer can still be imported by the CLI shell, just not by the server itself).

### Out of scope
- No config refactor (Phase 2).
- No file splitting inside `hooks.py` (Phase 3).
- No renames of curator A/B/C (Phase 4).

### Risks / unknowns
- `run_render` may pull in icons/rich-console state that's CLI-specific; if so, split into a `lore_runtime.render` (data) + `lore_cli.render` (presentation) seam.
- `argv_main` may be doing typer-specific argv munging; if it's truly a typer compat shim, it stays in `lore_cli` and the upward-importers should not need it — that becomes a "delete the import, refactor the caller" task instead of a "move the helper" task.

### Session log

**2026-04-25** — single sitting. Risks/unknowns above resolved cleanly:
`run_render` was pure stdlib (no rich deps), and `argv_main` is exactly
the typer-compat shim the docstring claimed it to be — so both files
moved into `lore_runtime` with no logic changes. The fence is real and
enforced by a new pytest guard.

**Landed**
- New package `lib/lore_runtime/` with `argv.py` (was `lore_cli/_compat.py`)
  and `run_render.py` (verbatim from the old location). Module docstring
  documents the layering rule explicitly.
- 24 importers migrated mechanically across `lib/` and `tests/`:
  `lore_core/lint.py`, `lore_core/migrate.py`, `lore_curator/curator_c.py`,
  `lore_mcp/server.py`, `lore_search/cli.py`, plus 18 sites inside
  `lore_cli/` that previously referenced their own `_compat.py`, plus
  `tests/test_run_render.py`.
- `lib/lore_cli/_compat.py` and `lib/lore_cli/run_render.py` deleted —
  no shim left behind. Clean break.
- `tests/test_layering.py` parametrizes over seven lower-layer packages
  (`lore_core`, `lore_curator`, `lore_mcp`, `lore_search`, `lore_sinks`,
  `lore_adapters`, `lore_runtime`) and fails the build if any of them
  contains a `from lore_cli...` or `import lore_cli...` statement.
  Static-only check — catches lazy-imports inside functions too.
- Pre-existing `tests/test_hooks_v2.py` rot fixed (4 sites): assertion
  changed from `"lore: active"` to `": active"` so it survives both the
  versioned (`lore 0.9.0: active`) and unversioned forms.

**Tests:** 1435 → 1466 passing (+7 layering guards, +24 re-enabled
hooks_v2 cases). Full suite runs cleanly with no skips or new
warnings; `python -m lore_cli --help` still renders the full subcommand
tree.

**Deliberate non-goals (deferred to a future phase)**
The roadmap's Phase 1 scope also mentioned "stop registering
`curator_c.app` and `mcp_cmd.app` as nested typer apps in
`lore_cli/__main__.py:48`; expose them as library entrypoints." This
was *not* done. Reasoning: the typer apps still living in
`lore_curator/curator_c.py`, `lore_mcp/server.py`,
`lore_search/cli.py`, `lore_core/lint.py`, and `lore_core/migrate.py`
are functional and now depend only on `lore_runtime` (not `lore_cli`)
— the fence is established. Migrating the typer-app construction into
new `lore_cli/<verb>_cmd.py` shells is a meaningful additional
refactor (5 files, risk of CLI breakage) that doesn't change the
architectural picture established by Phase 1. Park as Phase 1.5 if
the multi-host or library-mode use case ever materializes.

**Surprised**
- Only 5 of the 24 importers were *actually* lower-layer (the
  upward-dependency violators); the other 19 were `lore_cli` modules
  importing their own internal `_compat.py`. Same fix, but the
  architect's "4 importers" count understated the migration footprint.
- `lore_search/cli.py:19` was a fifth lower-layer importer the
  architect's review missed. Caught it via `grep -rn` before doing
  the move.
- The `_compat.py` docstring contained an example that *itself* used
  the old import path; the example would have been the only stale
  reference if I'd kept the file as a shim. Glad I deleted it cleanly.

**Scope refinements for Phase 2**
- Phase 2 (config consolidation) is unchanged in scope — `lore_runtime`
  doesn't touch config.
- The `lore_cli` decomposition that was deferred (above) is a
  candidate for "Phase 1.5" if it becomes the load-bearing concern
  during Phase 4 (naming + concept consolidation) where curator
  module renames happen.

---

## Phase 2 — Config consolidation *(sketch — refine when next)*

**Status:** ☐ pending — **not yet planned in detail.**

### Sketch
Consolidate the 9 sources of config truth into a documented precedence chain. Either adopt `dynaconf` (per project guidance) or document the existing layering explicitly with a precedence test. Unify the three "scope" stores (`_scopes.yml`, `scopes.json`, `attachments.json`) behind one resolver. Fix `LORE_ROOT` import-time resolution footgun. Pick one user-facing name from {wiki, scope, vault} and migrate help/error copy.

### Refine before starting
- Decide: `dynaconf` adoption vs. documented status quo. (`dynaconf` is in the global guidance but adds a dep — weigh.)
- Decide: which of the three scope stores wins, or do they collapse into one shape?
- Decide: do we deprecate `WIKI_ROOT` or keep it as a derived value?
- Sketch a migration plan for existing vaults (don't break installed users).

### Session log
*(empty)*

---

## Phase 3 — `hooks.py` decomposition *(sketch)*

**Status:** ☐ pending — **not yet planned in detail.**

### Sketch
Split the 2023-line `lib/lore_cli/hooks.py` into `hooks/{cache,proc,render,dispatch,gh}.py`. Replace ~25 `except Exception: pass` with specific exception types (mostly `OSError` and `KeyError` based on the surrounding code). Make `_pid_alive` cross-platform (macOS via `kill(pid, 0)` instead of `/proc`). Verify lockfile usage on `hook-events.jsonl`. Collapse the two parallel SessionStart hook pipelines (legacy + capture) into one.

### Refine before starting
- Decide the module boundaries — what does each new file own, exactly?
- Decide the deprecation cutoff for `_legacy_cache_path` and `migrate_legacy_pending_breadcrumb`.
- Audit which `except Exception` blocks are actually defensive vs. accidental — there may be 2-3 that *should* stay broad.

### Session log
*(empty)*

---

## Phase 4 — Naming + concept consolidation *(sketch)*

**Status:** ☐ pending — **not yet planned in detail.**

### Sketch
Curator A/B/C → role-named modules (e.g. `session_curator.py` / `daily_curator.py` / `defrag_curator.py`) so code matches user-facing copy. Decide the surface taxonomy: are `concept`/`decision`/`result` deprecated by surfaces? Document the cut and update README. CLI verb consistency: `lore surface add` ↔ `/lore:surface-add` (drop `-new`). Reconsider `new-wiki` → `wiki new`. Skills cite `lore lint` not `python -m lore_core.lint`.

### Refine before starting
- Decide naming for the three curators (role names that survive future surfaces).
- Decide the surfaces-vs-concept/decision/result cut — is it a rename, a deprecation, or a parallel taxonomy?
- Plan migration for existing notes that have the old types in frontmatter.

### Session log
*(empty)*

---

## Phase 5 — UX polish *(sketch)*

**Status:** ☐ pending — **not yet planned in detail.**

### Sketch
SessionStart reorder (status → focus → open items → directive last). MCP error envelope standardize (`{"error": {"code", "message", "next"}}`). `lore --help` grouping (promote `init`/`new-wiki`/`lint` out of `_Advanced_`). "Show me state" triad disambiguation (rename `/lore:context` → `/lore:loaded`, add `/lore:status`, cross-link status ↔ doctor). SKILL.md descriptions retrofit to *what / returns / when*. Inline `› consulted [[note]]` made deterministic.

### Refine before starting
- Validate the `/lore:context` rename doesn't break muscle memory (or alias both for a release).
- Decide the deterministic-citation mechanism: hook-emitted line, or extension to SessionStart cache?

### Session log
*(empty)*

---

## Phase 6 — Test hygiene + curator decomposition *(sketch)*

**Status:** ☐ pending — **not yet planned in detail.**

### Sketch
Delete `conftest.py:18-20` autouse-legacy-mode fixture; fix the tests that actually need `llm_only`. Add integration test for cascade default. Decompose `run_curator_c` (180-line god-function) into `_filter_already_ran`, `_apply_actions`, `_run_defrag_phase`, `_finalize_diff_logs` with one test per piece. Replace `try/except: pass` at ledger-update with explicit error path.

### Refine before starting
- Audit which tests legitimately need `llm_only` after the fixture is removed.
- Decide the granularity of the curator_c decomposition — could go further (per defrag pass) or stop at the four chunks.

### Session log
*(empty)*

---

## Phase 7 — Performance + scaling prep *(sketch, optional)*

**Status:** ☐ pending — **only if pain shows up before this point.**

### Sketch
Lazy-load cmd modules in `lore_cli/__main__.py`. Make `reindex(wiki=wiki)` conditional on mtime tick in MCP search. Plan O(N²) curator passes for windowed/incremental operation. Concurrent-write safety on `hook-events.jsonl`.

### Refine before starting
- Measure first. Don't refactor for performance without numbers.

### Session log
*(empty)*

---

## Cross-phase invariants

These hold throughout — no phase is allowed to violate them:

- **Markdown + git is authoritative.** Indexes/caches are derivable. Any new state added must respect this.
- **Hooks must remain fast.** SessionStart budget is <100ms in `hooks.py:6`. Any phase that touches hook code re-checks this.
- **No regressions in the user-facing slash/CLI surface.** If a name changes, the old name stays as an alias for at least one release.
- **Tests are the contract.** No phase ships without tests for the changes it made.

---

## Phases that *won't* happen (explicit non-goals)

- ❌ Rewrite from scratch.
- ❌ Replace SQLite FTS5 with a different search backend.
- ❌ Replace MCP/STDIO with HTTP.
- ❌ Migrate off typer.
- ❌ Redesign the curator triad concept (only rename + decompose).
