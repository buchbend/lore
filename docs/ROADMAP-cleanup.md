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
- ☑ **Phase 2** — Config map + state map + `require_lore_root()` *(2026-04-25)*
- ☑ **Phase 3** — Hook hygiene (pid_alive, except audit, lockfile docs) *(2026-04-26)*
- ☑ **Phase 4** — Skill ↔ CLI drift fix + surface-add slash rename *(2026-04-26)*
- ☑ **Phase 5** — UX polish (SessionStart reorder, --help groups, MCP envelope) *(2026-04-26)*
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

## Phase 2 — Config map + state map + `require_lore_root()`

**Status:** ☑ completed 2026-04-25
**Estimated session length:** 1 sitting

### Refined scope (made during the planning sub-step)

The "9 sources of config truth" enumerated by the review turned out to
be more disciplined than the review implied: `root_config.py` is
already a centralized layer with documented env precedence, and the
"three scope stores" have **genuinely distinct roles**, not duplicated
ones. The fix is therefore *legibility* — making the existing
discipline visible — plus the small amount of consolidation that's
actually duplicated.

Decisions made up front:
- **No dynaconf.** Overkill for the existing layered configs that
  don't actually conflict. The shared resolution pattern
  (`_resolve_mode`, `_resolve_openai_settings`, `_resolve_backend`)
  is already idiomatic and well-tested.
- **Keep the three scope/state files.** They have distinct roles:
  wiki-internal catalog (`_scopes.yml`), vault-wide derived registry
  (`scopes.json`), per-host consent record (`attachments.json`).
  Documenting the collaboration is more useful than collapsing them.
- **`LORE_ROOT`/`WIKI_ROOT` import-time footgun**: already removed in
  Phase 0 (module-level constants deleted). No further work needed.
- **User-facing naming**: vault / wiki / scope are *already* used
  consistently in user-facing copy. The deeper identifier rename
  (curator A/B/C → role names, etc.) is Phase 4 territory.

### Landed

- **`docs/architecture/config.md`** — canonical config map: every
  env var, every config file, full precedence chain, "adding a new
  setting" checklist. ~150 lines, table-driven.
- **`docs/architecture/state.md`** — canonical state map: the three
  `_scopes.yml` / `scopes.json` / `attachments.json` files, their
  distinct roles, regenerability table, collaboration diagram, and
  failure-mode descriptions. Includes a "Vocabulary" section
  codifying vault / wiki / scope.
- **`require_lore_root()` + typed exceptions** — added to
  `lore_core.config` alongside existing `get_lore_root()`. Two-layer
  resolver: `LoreRootError` base + `LoreRootNotSet` /
  `LoreRootMissing` specifics. CLI side now goes through
  `lore_cli/_cli_helpers.lore_root_or_die(err_console)`, replacing
  five 6-9 line per-file `_lore_root_or_die()` definitions with
  2-line delegating wrappers. Exit code standardized to 2
  ("incorrect usage / configuration error"). One pre-existing test
  asserting exit-code 1 was updated with a comment explaining the
  change.
- **`tests/test_config_resolvers.py`** (9 tests) — covers
  `get_lore_root` default-vs-env-set, `require_lore_root` happy
  path, `LoreRootNotSet` on unset/empty, `LoreRootMissing` on
  missing path, `~` expansion, common-base inheritance.
- **`tests/test_openai_precedence.py`** (5 tests) — pins down the
  env > config precedence chain for `LORE_OPENAI_BASE_URL`,
  `LORE_OPENAI_MODEL_{SIMPLE,MIDDLE,HIGH}`, including a
  partial-override test that proves env-set tiers don't blank out
  unset tiers (a regression risk in any future refactor).

**Tests:** 1466 → 1480 passing (+14).

### Already covered by existing tests (verified, no new tests needed)

- `LORE_NOTEWORTHY_MODE` env > config > default, plus
  garbage-fallback (`tests/test_noteworthy.py:287-341`).
- `LORE_LLM_BACKEND` cli > env > config > default
  (`tests/test_openai_backend.py:653-688`).
- `LORE_LLM_BACKEND=openai` env-only path
  (`tests/test_openai_backend.py:591-602`).
- `pyproject.toml` / `plugin.json` / `CHANGELOG.md` version triple
  (`tests/test_version_sync.py`, from Phase 0).

### Surprised

- The "9 sources of truth" framing was misleading. Each source has a
  legitimate distinct role (env vars, root config, wiki config,
  plugin manifest, install templates, frontmatter, etc.). The fix
  is documentation, not consolidation.
- The "three scope stores" framing was also misleading — they
  collaborate. The architect's "five files implementing one
  mapping" was wrong. The actual model is: catalog +
  derived-registry + consent-record. State map doc spells it out.
- The `_lore_root_or_die()` duplication was real and worth fixing,
  even though it's small (5 files × ~6 lines). The exit-code drift
  (some used 1, some used 2, with the 2-using ones doing the
  fuller existence check) was a subtle inconsistency that the
  consolidation surfaced.
- One pre-existing test failure surfaced and was fixed inline
  (`tests/test_cli_attachments.py:126` — exit-code drift from 1 to
  2 after standardization).

### Scope refinements for Phase 3

- Phase 3 is `hooks.py` decomposition (still a sketch). The
  layering fence (Phase 1) means `hooks.py` can now be split
  without lower layers needing to follow.
- Pre-existing `test_hooks_v2` rot was already fixed in Phase 1.
  Phase 3 inherits a hook surface area in working order.
- The `_resolve_*` pattern (env > config > default) documented in
  `docs/architecture/config.md:"Adding a new setting"` should be
  the template if Phase 3 adds new env-overridable hook settings.

---

## Phase 3 — Hook hygiene (pid_alive, except audit, lockfile docs)

**Status:** ☑ completed 2026-04-26
**Estimated session length:** 1 sitting

### Refined scope (made during the planning sub-step)

The original sketch proposed splitting `hooks.py` into 5 modules
(`cache/proc/render/dispatch/gh`). After auditing the file, the split
was **deferred**:

- The 2023 lines have clean section dividers; readability isn't the
  load-bearing problem.
- ~30 internal cross-references and an externally-consumed public API
  (the typer commands + module-level helpers tests mock by name) make
  splits high-risk for pure cosmetic gain.
- The layering fence from Phase 1 already broke the *architectural*
  problem (lower layers no longer reach into hooks).
- Two of the original "concerns" turned out to be misreads: the
  parallel SessionStart hooks aren't duplicates (different
  responsibilities — context injection vs. capture telemetry), and
  the `hook-events.jsonl` "interleave-corruption risk" is already
  addressed by `O_APPEND` atomic writes + flock-guarded rotation.

So Phase 3 was redirected to the genuine bugs and visibility gaps.

### Landed

- **`_pid_alive` cross-platform fix.** Replaced the `/proc`-walk
  (which returned `True` conservatively on macOS, meaning per-PID
  caches never GC'd) with `os.kill(pid, 0)`. Documents POSIX
  semantics (ESRCH → False, EPERM → True, other OSError →
  conservative True). 6 new tests in `tests/test_hooks_pid_alive.py`
  cover self-pid, zero/negative guards, all three errno paths, and a
  real dead-PID end-to-end check.
- **Broad-except audit.** Touched 9 sites in `hooks.py`:
  - `_lore_version` → `PackageNotFoundError`
  - `_wiki_hints` yaml load → `(OSError, yaml.YAMLError)`
  - `_nudge_unattached` attachments load → `(OSError, json.JSONDecodeError)`
  - vault-equality check → `OSError`
  - banner catalog `.get()` chain → `(KeyError, TypeError, AttributeError)`
  - drain-lines + cross-scope-breadcrumbs → `(OSError, json.JSONDecodeError)`
  - offer-notice + outer banner: kept broad with explicit
    `# noqa: BLE001 - hook must never crash SessionStart` comments
    because they are presentation-layer wraps where defensive
    behaviour is the contract.
- **Lockfile discipline made visible.** The grumpy-dev concern about
  hook-events.jsonl interleave-corruption was *already addressed* —
  documented now in the `hook_log.py` module docstring (POSIX
  `O_APPEND` atomicity + `fcntl.LOCK_EX | LOCK_NB` rotation lock)
  and surfaced in `docs/architecture/state.md` as the canonical
  lockfile pattern used across the codebase.

**Tests:** 1480 → 1486 passing (+6 from `test_hooks_pid_alive.py`).
No regressions.

### Deliberate non-goals (not deferred — actively decided not to do)

- **File splitting** — clean section dividers + tests that mock
  module-level helpers by name make this a high-risk cosmetic move.
  If a future phase has a structural reason to split (e.g. Phase 4's
  curator rename brings related changes), bundle it then.
- **"Collapse the two parallel SessionStart hook pipelines"** —
  reading the code, they have different responsibilities (one
  injects context, the other records hook firings for telemetry).
  Coupling them would lose the separation. Left independent by
  design.

### Surprised

- The grumpy-dev review said "concurrent multi-process writes will
  eventually interleave-corrupt without a lockfile" for
  `hook-events.jsonl`. They were *wrong*: the existing
  implementation uses POSIX-atomic `O_APPEND` + flock-guarded
  rotation. The audit flagged a fix that wasn't needed; the *fix*
  ended up being making the existing discipline visible in docs.
  Good lesson: code reviews benefit from reading the implementation
  before flagging a "missing" defensive measure.
- The `_pid_alive` bug was real and load-bearing on macOS — not
  just "conservative-true" but *systematically*-true. Stale caches
  would build up indefinitely. The 14-day max-age fallback in
  `_gc_sessions_cache` is what kept this from being catastrophic;
  worth knowing that fallback exists.
- 9 broad-except sites felt like a lot until the audit; only ~5 of
  them were actually accidental. The rest are legitimate
  presentation-layer "must never crash hook" wraps. Adding `# noqa`
  comments turned the silent broad-catch into an *intentional*
  broad-catch, which is the right outcome.

### Scope refinements for Phase 4

- Phase 4 is naming + concept consolidation. With the hook surface
  now hygienic, the curator A/B/C → role-name rename can land
  without touching hook plumbing.
- The deferred file-split for `hooks.py` should be revisited only
  if Phase 4 or Phase 5 produces an organic reason to touch the
  file's structure (e.g. role-renamed curator spawn helpers want
  to live next to capture).

---

## Phase 4 — Skill ↔ CLI drift fix + surface-add slash rename

**Status:** ☑ completed 2026-04-26
**Estimated session length:** 1 sitting

### Refined scope (made during the planning sub-step)

The original sketch packaged five concerns: curator A/B/C rename,
surface-vs-concept/decision/result deprecation, `new-wiki` →
`wiki new`, surface-new → surface-add slash, skill ↔ CLI drift.
After verifying each claim against the actual code:

- **Curator A/B/C rename**: cosmetic — the role mapping is *already*
  documented in `lore_curator/__init__.py`'s docstring. Renaming
  modules would touch ~200 import sites for zero behavioural gain
  and high risk of breaking tests that mock helpers by name.
  **Deferred** — the docstring serves the user-mental-model purpose.
- **`concept`/`decision`/`result` deprecated by surfaces**: claim was
  **wrong**. Verified by grepping the live vault: 30+ active notes
  with `type: concept`. Surfaces and the older types coexist —
  surfaces are template-driven extraction; concept/decision/result
  are direct hand-written types. Both are valid and there's nothing
  to deprecate.
- **`new-wiki` → `wiki new`**: cosmetic. Real users have muscle
  memory for `lore new-wiki` and external scripts; renaming requires
  a deprecation cycle (alias both, warn, eventually remove). High
  friction for low payoff. **Deferred**.
- **Skill drift** (`python -m lore_core.lint` etc.): real, small,
  load-bearing for skill UX (skills currently leak internal package
  paths). **Done.**
- **`/lore:surface-new` → `/lore:surface-add`**: real CLI/slash
  asymmetry. Small fix. **Done.**

### Landed

- **Skill drift cleared.** `skills/lint/SKILL.md` (5 sites) now uses
  `lore lint` instead of `python -m lore_core.lint`; 3 sites use
  `lore migrate` instead of `python -m lore_core.migrate`.
  `skills/curator/SKILL.md` (2 sites) uses `lore curator` instead of
  `python -m lore_cli curator`. Both verified via `lore <verb>
  --help` to confirm flags match.
- **Slash rename: `/lore:surface-new` → `/lore:surface-add`.**
  Renamed `skills/surface-new/` → `skills/surface-add/` with `git
  mv` (history preserved). Updated SKILL.md `name:` frontmatter,
  body references, cross-references in `surface-init/SKILL.md` and
  `README.md`, and the launcher in `lib/lore_cli/surface_cmd.py:79`
  (which exec's `claude "/lore:surface-add <wiki>"` now). Existing
  test in `tests/test_cli_surface.py` updated to assert the new
  invocation. CHANGELOG entry added (user-visible slash-autocomplete
  change).
- **Drift guard test** (`tests/test_skill_cli_drift.py`, 2 tests):
  static check that no SKILL.md re-introduces `python -m lore_<x>`,
  plus a heuristic scanner that flags possible CLI verb drift
  against the live `lore --help` output. Catches future drift
  without manual maintenance.

**Tests:** 1486 → 1488 passing (+2 drift guard). No regressions.

### Deliberate deferrals (recorded for future)

- **Curator A/B/C module rename.** ~200 import sites; the role
  mapping is already documented in `__init__.py`. Revisit only if
  a refactor in another phase produces an organic reason to touch
  those modules en masse.
- **`new-wiki` → `wiki new` CLI rename.** Needs a deprecation cycle
  (alias both for ≥1 release, warn-on-old, eventually remove).
  Plan that once 1.0 is on the horizon — pre-1.0 these renames are
  legitimate but post-1.0 they need user comms.

### Surprised

- The "concept/decision/result vs surfaces" claim was inverted —
  the review framed them as duplicating each other; reading the
  vault showed they're a parallel taxonomy. Direct types
  (concept/decision/result) are hand-written or extracted-without-
  template; surfaces are template-driven. Neither replaces the
  other. Recording this so future reviewers don't re-flag it.
- `lore_curator/__init__.py` already had a clean docstring mapping
  Curator A → session notes, B → surface extraction, C → weekly
  defrag/converge. The "code identifiers don't match user copy"
  concern was addressed by docs *before* I got there — typical for
  a project that writes its own dogfood.
- Slash command rename was small (4 files + 1 changelog) but the
  cross-references took some chasing. Glad I checked
  `surface_cmd.py:79` and the existing test before declaring done
  — the launcher would have shipped pointing at a non-existent
  slash otherwise.

### Scope refinements for Phase 5

- Phase 5 is UX polish (SessionStart reorder, MCP error envelopes,
  help-grouping). Phase 4's slash-rename is a precedent for the
  kind of small user-visible churn Phase 5 will involve.
- The drift guard added in Phase 4 is the test pattern for Phase 5
  too — when reordering SessionStart or standardising MCP errors,
  add static tests that pin down the new shape.

---

## Phase 5 — UX polish (SessionStart reorder, --help groups, MCP envelope)

**Status:** ☑ completed 2026-04-26
**Estimated session length:** 1 sitting

### Refined scope (made during the planning sub-step)

The original sketch packaged six concerns. After verifying claims:

- **SessionStart reorder**: real, simple. **Done.**
- **`lore --help` grouping**: real (`init`/`new-wiki`/`lint` in
  Advanced). **Done.**
- **MCP error envelope**: 13 bare-string returns, 1 partially-shaped
  return (line 239), 2 JSON-RPC ones (correct as-is). **Done** for
  the 8 tool-handler call sites that benefit from a code-keyed shape.
- **`/lore:context` → `/lore:loaded` rename**: deferred — needs
  deprecation alias for muscle memory; not worth the user comms
  pre-1.0.
- **SKILL.md description retrofit to *what/returns/when***:
  deferred — bounded but tedious; lower-value than the structural
  fixes that landed.
- **Inline `› consulted [[note]]` deterministic**: deferred —
  needs a hook-side mechanism (extension to SessionStart cache or
  a new tool-postprocess hook), bigger change than fits in Phase 5.

### Landed

- **SessionStart directive moved to postscript.** Both
  `_session_start_from_lore` (line 643) and `_session_start` (line
  755) now order: status line → focus → open items → directive
  postscript. Status + context render *first*, the rule reasserts
  itself at the bottom without competing for the most-attention
  slot. Updated `tests/test_hooks_v2.py` with a positional assertion
  pinning the new ordering (status pos < issues pos < directives
  pos). The existing 24 hooks_v2 tests + 4 ordering checks all
  pass.
- **`lore --help` re-grouped.**
  - Getting Started gains `init` (was Advanced) — a first-run user
    types `lore --help` and now sees `init` two lines below
    `install`, where they need it.
  - Knowledge gains `lint`, `new-wiki`, `curator` (all from
    Advanced) — these are routine vault-hygiene verbs, not
    developer-only tooling.
  - Advanced still hosts `proc`, `runs`, `transcripts`, `mcp`,
    `migrate`, `hook`, etc. — actual internal/diagnostic verbs.
- **MCP error envelope standardized.** New `_mcp_error(code,
  message, next_=None)` helper at the top of `lore_mcp/server.py`.
  Migrated 8 tool-handler call sites (`handle_read`, `handle_index`,
  `handle_catalog`, `handle_wikilinks`, dispatcher's unknown-tool
  fallthrough). Codes used: `wiki_not_found` (with "run `lore
  status`" hint), `note_not_found`, `path_escape`, `path_not_found`,
  `catalog_missing` (with "run `lore lint`" hint), `unknown_tool`.
  The pre-existing `lore_surface_validate` issue list (which
  already had structured `{level, code, message}`) was cited in the
  helper docstring as the precedent.
- **Tests:** `tests/test_mcp_error_envelope.py` (8 tests) — pins
  helper basics, every `_mcp_error` envelope shape, and the
  recovery-hint contract. Updated
  `tests/test_mcp_read_wikilink.py:75` from the old bare-string
  assertion to the new envelope shape.

**Tests:** 1488 → 1496 passing (+4 ordering checks in hooks_v2,
+8 error-envelope tests). No regressions.

### Deliberate deferrals

- **`/lore:context` → `/lore:loaded` slash rename.** Pre-1.0 we
  could just rename, but a user who has typed `/lore:context` in
  the past month gets a "command not found" surprise. Better to
  alias both for one release, deprecation-warn, then remove. Park
  for the 1.0 release-prep pass.
- **SKILL.md description normalisation.** The 17 user-facing
  skills have description fields ranging 18-41 words; reformatting
  all of them to a *what / returns / when* template is achievable
  but mostly cosmetic. The drift guard added in Phase 4 catches
  the worst issue (skills citing internal package paths); pure
  description quality can wait.
- **Deterministic inline citations.** The `› consulted [[X]]`
  affordance is currently agent-discretional (Claude renders it
  when it remembers, drops it when it doesn't). Making it
  deterministic would mean either a) emitting it from a hook
  whenever `lore_search` is called, or b) extending the
  SessionStart cache to record citation metadata that the agent
  reads back. Both are bigger architectural changes than fit in
  Phase 5; revisit if/when citation reliability becomes a user
  complaint.

### Surprised

- The MCP error landscape was *messier* than the review showed:
  three different error shapes coexisted (bare string at most call
  sites, partial-response-with-error at line 239 in
  `handle_surface_context`, structured JSON-RPC at the dispatcher).
  Migrating to one shape across all three layers would have been
  too invasive — instead the helper is scoped to tool handlers and
  the docstring documents that the JSON-RPC layer is intentionally
  different.
- The `/lore:context` rename was tempting but the mental cost of
  deprecating-and-aliasing for one release outweighed the
  user-facing clarity gain. Pre-1.0, every "small" rename like this
  costs more than it looks like.
- The SessionStart reorder was a 6-line change and arguably the
  highest-impact UX win in the whole roadmap so far — the directive
  was *first* on every banner since Lore shipped, and reading the
  banner with directive-last feels noticeably different (status +
  payload first, rule postscript at the bottom). Worth the
  deferral discipline that kept the original sketch from bloating.

### Scope refinements for Phase 6

- Phase 6 is curator decomposition + test hygiene (drop the
  autouse legacy-mode fixture; integration test for cascade
  default; decompose `run_curator_c`'s 180-line god-function).
- The `_mcp_error` helper pattern is reusable in Phase 6 if any
  curator code emits structured error payloads — same envelope
  works for "run-summary error rows" if that's wanted.
- Phase 5's deferred items (`/lore:context` rename, citation
  determinism) are candidates for a "1.0 release prep" pass after
  Phase 7, not for Phase 6.

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
