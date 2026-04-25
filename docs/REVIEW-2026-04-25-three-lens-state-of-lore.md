# State of Lore — Three-Lens Report

**Date:** 2026-04-25
**Reviewed at:** v0.9.0 (`pyproject.toml`) / 0.5.0 (`.claude-plugin/plugin.json`)
**Method:** Three independent sub-agents (grumpy senior dev / senior architect / UI/UX critic) reviewed the codebase in parallel without seeing each other's work. Findings below lead with cross-cutting convergence (strongest signal), then each lens's distinct contributions.

---

## 1. Cross-cutting findings (multiple agents converged)

These are items where two or three lenses arrived at the same conclusion through different paths. Treat as load-bearing.

### A. The plugin manifest version is stale and the discipline isn't enforced

- `pyproject.toml:3` → `0.9.0`. `.claude-plugin/plugin.json:4` → `0.5.0`. `CHANGELOG.md` is a third version source.
- `CONTRIBUTING.md:134-141` says they MUST move in lockstep; the project's own memory note (`project_lore_plugin_cache_stale`) flags this as a known footgun.
- Every Claude Code install of Lore between 0.5.1 and 0.9.0 has been silently running cached code. **5-line fix + CI guard.** Highest ROI in the report.

### B. Config has nine sources of truth and no documented precedence

Both grumpy and architect enumerated the same fragmented surface area:

1. CLI flags / env (`LORE_ROOT`, `LORE_NOTEWORTHY_MODE`, `LORE_LLM_BACKEND`, `LORE_TRACE_LLM`, `LORE_CACHE`, `ANTHROPIC_API_KEY`)
2. `$LORE_ROOT/.lore/config.yml` (`root_config.py`)
3. `<wiki>/.lore-wiki.yml` (`wiki_config.py`)
4. `<repo>/CLAUDE.md ## Lore` block
5. `.claude-plugin/plugin.json`
6. `pyproject.toml` (+ `CHANGELOG.md` as a third version mirror)
7. Note frontmatter
8. `lib/lore_cli/hosts.d/*.toml`
9. `<wiki>/_scopes.yml` **and** `<lore_root>/.lore/scopes.json` **and** `attachments.json` (three "scope" stores)

`lore_core/config.py:30` resolves `LORE_ROOT` at module-import-time *and* exposes a getter — tests that override env after import don't see the change at the module-level constants. No `dynaconf` despite project guidance.

### C. Three names for "scope" with three implementations

- `lore_core/scopes.py` (per-wiki `_scopes.yml`, `walk_scope_leaves`)
- `lore_core/state/scopes.py` (vault-wide `scopes.json`, `ScopeEntry` dataclass)
- `lore_core/scope_resolver.py` (cwd → wiki via `attachments.json`)

Conceptually one mapping; physically three formats. Architect flagged the CLI/MCP/skills boundary; grumpy flagged the file-format spread; UX flagged the user-facing "wiki vs scope vs vault" copy in `init_cmd.py:81` ("Vault root path (defaults to $LORE_ROOT or ~/lore)" — three names in one help string).

### D. `lore_cli` has stopped being a thin shell — it's the runtime hub

- `lib/lore_cli/hooks.py` is **2023 lines, 72 functions, ~25 broad excepts** (grumpy).
- `lore_core/lint.py:706`, `lore_core/migrate.py:15`, `lore_curator/curator_c.py:988`, `lore_mcp/server.py:826` all import `lore_cli._compat.argv_main` (architect). Lower layers reaching up — that's an inverted dependency.
- This is the single architectural debt that blocks every other refactor: the multi-host vision, library-mode use, integration testing of "core+curator" without typer.

---

## 2. Code quality & tech debt (grumpy dev)

### Top sharp edges

1. **Half-applied LLM-abstraction refactor.** `lore_curator/llm_client.py` exists; **48 occurrences of `anthropic_client`** still call `.messages.create(...)` with vendor-specific tool schemas. Worst: `curator_c.py:1182` passes the new abstract client through a parameter literally named `anthropic_client=`. The OpenAI backend is silently no-op or broken on every defrag pass.
2. **Test suite runs the wrong default.** `tests/conftest.py:18-20` autouse-monkeypatches every test to `LORE_NOTEWORTHY_MODE=llm_only`. Cascade was promoted to default in v0.6.0 (`d9e8289`). Production default is exercised only by tests that opt in.
3. **180-line god-function** at `curator_c.py:676-857` (`run_curator_c`): ledger filtering, merge pre-flight, hygiene, snapshotting, action application, defrag dispatch, diff logs, retention pruning — with `try/except: pass` at line 854 silently swallowing ledger update failures.
4. **TODO leaking into shipped output.** `lore_core/session.py:183` `BODY_TEMPLATE` writes literal `## What we worked on\n\n- TODO\n` into every freshly-scaffolded session note.
5. **86 broad `except Exception`** + 25 explicit `except: pass` in `lib/`. Hot spots: `curator_a.py:552`, `curator_b.py:404,462,517`, `c_orphan_links.py:166,219`, `c_auto_supersede.py:167,212,261`, `hooks.py` (16 separate lines).

### Stale modules / dead code

- `lib/lore_core/migration/` — empty directory, only `__pycache__`. Delete.
- `build/lib/lore_import/` — committed stale wheel artifact; `lore_import` doesn't exist in `lib/`.
- `breadcrumb.py:121 migrate_legacy_pending_breadcrumb` runs unconditionally; no deprecation marker.
- `hooks.py:63 _legacy_cache_path` and the legacy fallback at lines 825-843; same — when do we delete?

### Lazy local imports as architectural duct tape

`lore_cli/hooks.py:1037,1359,1842,1854` and `curator_c.py:697-699,1147,1188-1190,1212` repeat function-body `from lore_core.config import get_lore_root` 6+ times. Started as circular-import workarounds, never cleaned up.

### What's actually good

- `lore_core/io.py:atomic_write_text` used consistently for cache and ledger writes.
- `lore_curator/llm_client.py` itself is clean — the *design* is right; only the migration is incomplete.
- `lore_core/state/attachments.py` (`AttachmentsFile.longest_prefix_match`, `Scope` dataclass) is exactly the right shape — pure, testable, no walk-up filesystem magic.
- PID-keyed session cache in `hooks.py:51-72` — thoughtful concurrent-session design.
- `tests/test_root_config.py:39-54` is exemplary. More tests should look like that.
- Run logger context-manager pattern in `curator_c.py:728-736` (with `contextlib.nullcontext()` for the no-logger branch) is clean.

---

## 3. Architecture (senior architect)

### Architectural map (one-paragraph read)

Four tiers (plugin shape / typer CLI / deterministic core / LLM+search+MCP+sinks tier) but the layering is **a diamond, not a stack**: skills shell out to the CLI; the CLI dispatches into core/curator/search/MCP; but `lore_core`, `lore_curator`, and `lore_mcp` all import `lore_cli._compat` to be runnable as standalone CLIs. The "core" depends back up on the "shell."

### Concept fragmentation

Three overlapping naming systems in the vault:

- **Location**: `wiki` / `scope` / `attachment` / `LORE_ROOT` / `WIKI_ROOT`. Five files implementing the same cwd→wiki mapping.
- **Surfaces**: the new template-driven `surface` overlaps semantically with the older `concept`/`decision`/`result`/`session` taxonomy that the README still cites. Surfaces are now the unifying abstraction, but the older terms still exist in copy and code.
- **Curators**: A/B/C is documented one way (per-session / per-day / weekly), implemented another way (`curator_a.py`, `curator_b.py`, `curator_c.py` plus `c_*.py` policy files), and the user-facing copy says only "Curator." Three names for one concept depending on layer.

### Boundary violations

- **CLI ↔ core**: `lore_core/lint.py` and `lore_core/migrate.py` import typer helpers. Core should not know typer exists.
- **CLI ↔ curator**: `lore_cli/__main__.py:48` mounts `curator_c.app` as a typer subcommand — curator ships its own CLI. `curator_c.py:1078` reaches into `lore_cli.run_render` for icon/render helpers.
- **MCP ↔ skills**: `server.py:210,593` documents that MCP gathers, then the skill shells out to the CLI to write — three boundary crossings for one intent. Principled (visibility) but you can't drive Lore from a non-Claude MCP client end-to-end.
- **Skills ↔ CLI**: `/lore:context` (`skills/context/SKILL.md:18`) requires `dangerouslyDisableSandbox: true` to run `lore hook context-log`. Most brittle skill in the set.
- **Hooks ↔ everything**: `plugin.json` registers **two** SessionStart hooks and **two** PreCompact hooks (legacy `lore hook session-start` and newer `lore hook capture --event ...`). Both run in series. No single "session-start handler."

### What scales, what won't

- ✅ SQLite FTS5 + mtime/SHA incremental reindex; "markdown+git authoritative, indexes derived" invariant is strong.
- ⚠️ **SessionStart cold-start latency under multi-wiki**: two hooks × eager imports of all 30 cmd modules × N wikis. The "<100ms" budget in `hooks.py:6` will slip.
- ⚠️ **Unconditional `reindex(wiki=wiki)` on every MCP search call** (`lore_mcp/server.py:67`).
- ⚠️ **`curator_c.py` defrag is O(N²)-shaped** (adjacent-merge + auto-supersede + orphan-links) on a monotonically growing graph.
- ⚠️ **`hook-events.jsonl` shared append file** under `$LORE_ROOT/.lore/`; verify lockfile usage before multi-process.
- ⚠️ **`hooks.py:_pid_alive` is Linux-only** (`/proc` walk) — returns `True` conservatively on macOS, so heartbeat cache cleanup is effectively never on Mac.

### Strategic recommendation (architect's pick if you only do one thing)

Split `lore_cli` into `lore_cli` (thin typer shell) + `lore_runtime` (the hook/dispatch logic the CLI currently owns). Stop having `lore_core`, `lore_mcp`, and `lore_curator` import from `lore_cli`. **Every other consolidation downstream becomes local once that fence is up** — curator triad rename, surfaces vocabulary unification, config consolidation all become single-package refactors instead of cross-package surgery.

---

## 4. UX (CLI / slash / MCP / hook surfaces)

### Surface inventory at a glance

- **CLI verbs**: ~30 (install, attach, status, doctor, config, search, session, surface, news, resume, backfill, attachments, briefing, completions, curator, detach, hook, inbox, ingest, init, lint, log, mcp, migrate, new-wiki, proc, registry, runs, scopes, transcripts, uninstall)
- **Slash commands**: 19 advertised, **17 actually exist on disk**
- **MCP tools**: 11 (`lore_search`, `lore_read`, `lore_index`, `lore_catalog`, `lore_resume`, `lore_wikilinks`, `lore_session_scaffold`, `lore_briefing_gather`, `lore_inbox_classify`, `lore_surface_context`, `lore_surface_validate`)
- **Hooks visible to user**: SessionStart status line + body, PreCompact systemMessage, `lore status`, `lore doctor`, the directive in `templates/host-rules/default.md`

### Honesty gaps in advertised surface

- **`skills/on/SKILL.md` and `skills/loud/SKILL.md` do not exist on disk.** Documented as inverses inline in `off/` and `quiet/` SKILL.md files. The slash-namespace advertised to users (19 commands) is not the slash-namespace Claude Code actually has (17). Either ship the missing SKILL.md files or collapse the toggles.
- **`lore surface add`** vs **`/lore:surface-new`** — CLI verb is `add`, slash is `new`. Pick one.
- **`new-wiki` is the only hyphenated CLI verb.** Everything else is single-word or `surface init`. `lore wiki new` would be more consistent.
- **Skill drift:** `skills/lint/SKILL.md:21` tells the user `python -m lore_core.lint --json`, but the CLI exposes `lore lint`. `skills/curator/SKILL.md:48,51` calls `python -m lore_cli curator` — leaks package paths to users.

### Help/error inconsistency (representative)

- ✅ Good: `attach_cmd.py:152-159` scope-conflict shows three ordered next-step commands. Exemplary CLI error UX.
- ✅ Good: `hooks.py:684-688` "lore: no vault at LORE_ROOT=…. Set LORE_ROOT to your vault path or run `lore init`."
- ❌ Bad: `attach_cmd.py:97-100` for the *same* condition just says `"LORE_ROOT is not set."` — no next step.
- ❌ Bad: `lore_mcp/server.py:103-138` returns `{"error": "..."}` strings of inconsistent shape. Some include backticked next-step commands, some don't. `path escapes wiki root` tells the agent nothing actionable.
- ❌ Bad: `hooks.py:842` emits `_(legacy cache — may be from another session)_` — internal-implementation language leaking to users.

### Slash command bloat (19 is too many)

- **Four toggles for two binary states**: `/lore:on`, `/lore:off`, `/lore:quiet`, `/lore:loud`. Half don't exist on disk.
- **`/lore:context` vs `/lore:resume` vs `lore status` vs `lore doctor`** — four overlapping "show me state" commands. The names don't telegraph the difference. UX agent suggests:
  - `/lore:loaded` (cache, what came in this session — past tense)
  - `/lore:resume` (gather more — present-tense action)
  - `lore status` (is plumbing doing anything right now?)
  - `lore doctor` (is install broken?)
- **No `/lore:status`.** Symmetry break — users will type it.
- **`init`, `new-wiki`, `lint` are buried in `_Advanced_`** in `lore --help`, alongside developer-only verbs like `proc`, `runs`, `transcripts`. `init` is the very next thing a first-run user needs (per README). Promote.

### SessionStart — the best surface in the product

The status line `lore 0.9.0: active · [[private]] · last note: … · 2 issues · 1 PR` is exemplary: F-pattern friendly, every token earns its place, hard 2000-char body cap. **One reorder request**: the directive ("Asking about a wikilinked term without searching first is a bug") sits *above* the focus block. It scolds before showing what Lore did for the user. Move directive to the bottom or fold into the status line.

### MCP descriptions are stronger than SKILL.md descriptions

MCP descriptions consistently follow *what / what-it-returns / when*. SKILL.md descriptions vary wildly (18–41 words) and `/lore:curator` + `/lore:lint` overlap enough that Claude may struggle to pick. Retrofit the MCP template onto SKILL.md frontmatter.

### Top 5 UX fixes (ranked)

1. Fix the slash-command honesty gap (`on`/`loud` skills, or collapse toggles).
2. Promote `init`, `new-wiki`, `lint` to `_Getting Started_` / `_Knowledge_` in `lore --help`.
3. Standardize MCP error envelopes: `{"error": {"code": ..., "message": ..., "next": ...}}`. Pattern already proven in `lore_surface_validate:332-336`.
4. Reorder SessionStart: status → focus → open items → directive at bottom.
5. Tighten the "show me state" triad (rename `/lore:context` → `/lore:loaded`, add `/lore:status`, cross-link status ↔ doctor).

---

## 5. If you do nothing else this week

The three lenses converged on a clear top-3:

1. **Sync `plugin.json` version with `pyproject.toml` and add a CI guard.** 5-line fix; users are hitting it right now per the project's own memory notes.
2. **Rename `anthropic_client` → `llm_client` everywhere; route every `.messages.create` call through `LlmClient`.** The OpenAI backend is half-broken until this lands. Add a smoke test running defrag on the OpenAI backend.
3. **Ship `skills/on/SKILL.md` and `skills/loud/SKILL.md`** (or collapse the toggles). Highest-visibility user-facing inconsistency; trivial to fix.

Then plan the structural moves for next phase: `lore_cli` decomposition (architect's recommendation), `hooks.py` split into `hooks/{cache,proc,render,dispatch}.py`, the three-scopes consolidation, and SessionStart directive reorder.

---

## Appendix — key file paths referenced

**Code-quality hot spots**
- `lib/lore_cli/hooks.py` (2023 lines)
- `lib/lore_curator/curator_c.py:676-857` (180-line god-function), `:1182` (`anthropic_client=` rename leak)
- `tests/conftest.py:18-20` (autouse legacy-mode override)
- `lib/lore_core/session.py:183` (TODO in template)
- `lib/lore_core/migration/` (empty, delete)
- `build/lib/lore_import/` (stale build artifact)

**Architectural inversions**
- `lib/lore_core/lint.py:706`, `lib/lore_core/migrate.py:15`, `lib/lore_curator/curator_c.py:988`, `lib/lore_mcp/server.py:826` — all import `lore_cli._compat`
- `lib/lore_core/config.py:30` — `LORE_ROOT` resolved at import-time
- `.claude-plugin/plugin.json` — two parallel SessionStart hooks
- `lib/lore_mcp/server.py:67` — unconditional `reindex` per search call

**UX surface**
- `skills/on/SKILL.md` (missing), `skills/loud/SKILL.md` (missing)
- `skills/lint/SKILL.md:21` (`python -m lore_core.lint` — should be `lore lint`)
- `skills/curator/SKILL.md:48,51` (`python -m lore_cli curator` — leaks package path)
- `lib/lore_cli/attach_cmd.py:97-100` vs `lib/lore_cli/hooks.py:684-688` (inconsistent error guidance for same condition)
- `lib/lore_mcp/server.py:103-138` (inconsistent error envelopes)
- `templates/host-rules/default.md` (directive tone + position)
- `lib/lore_cli/__main__.py:54-57,86,87,91` (help-grouping promotes wrong verbs)
