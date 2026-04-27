# Lore Simplification Review (post-v0.10.2)

Phases 0–8 swept the obvious targets honestly: `run_curator_c` is 145 lines (was 237), version sync is real, the `lore_runtime` layering fence is enforced by `tests/test_layering.py`. Below is what survived.

---

## Top 8 simplification opportunities

Ranked by `(LOC reduction × clarity gain) / risk`.

### 1. Consolidate `_split_frontmatter` / inline frontmatter parsing into `lore_core/schema.py`

- **Files:** `lib/lore_core/schema.py:107-117` (canonical), `lib/lore_core/migrate.py:24-33` (dup), `lib/lore_curator/defrag_curator.py:294-300` (dup), `lib/lore_mcp/server.py:338-346` (inline reimpl), `lib/lore_curator/abstract.py:256-288` (specialised "strip-leading" variant — keep, but build on the shared splitter)
- **What:** Promote one `split_frontmatter(text) -> tuple[str, str, str] | None` returning `(opening_fence_offset_block, fm_yaml, body)` and delete the two dups. `parse_frontmatter` keeps its current shape and calls the splitter. MCP's inline `txt.startswith("---\n") / find("\n---\n", 4)` block becomes one call.
- **LOC:** ~40 → ~15 (5 sites collapse to 1). 
- **Risk:** Low. All five implementations agree on the `---\n…\n---\n` shape; only `abstract.py` differs (it adds yaml-validates-as-dict guard) and that guard stays. Existing tests pin parse semantics.

### 2. Merge `lore_sinks` into `lore_core/briefing/`

- **Files:** `lib/lore_sinks/` (237 LOC, only `markdown.py` 53 LOC + `matrix.py` 161 LOC, plus a 23-line `__init__.py` advertising sinks that don't exist — Slack, Discord, GitHub Discussion)
- **What:** The package's docstring promises 5 sinks; ships 2. `markdown.py` is a 24-line `atomic_write_text` wrapper + an argparse main. `matrix.py` is a single API client. The `BriefingSink` Protocol has zero registry pattern — `briefing_cmd.py` already does `if sink == "matrix": ...`. Move both files under `lore_core/briefing/sinks/` and drop the Protocol; bring back when a third sink lands.
- **LOC:** 237 → ~190 (kill aspirational Protocol + redundant `__init__`; consolidate argparse mains into one shared entrypoint). 1 fewer top-level package.
- **Risk:** Low. Two consumers (`briefing_cmd.py` + `lore-sink-*` argv main). Tests `tests/test_sinks_*.py` follow the import.

### 3. Inline the four `_gh_*` wrappers in `lore_cli/hooks.py`, but keep `_run_gh`

- **Files:** `lib/lore_cli/hooks.py:587-608`
- **What:** `_split_filter`, `_format_issue_line`, `_format_pr_line` are one-line passthroughs to `lore_core.gh.*`. They exist for monkeypatch symmetry, but a grep of `tests/` shows only `_run_gh` is actually monkeypatched (`tests/test_hooks_v2.py:299,329`). Inline the other three; keep `_run_gh` for the monkeypatch contract and document why.
- **LOC:** ~22 → ~6.
- **Risk:** Low. Run `pytest tests/test_hooks_v2.py` after — if any test patches `_split_filter`/`_format_*`, restore those.

### 4. Move 5 lower-layer typer apps into `lore_cli/<verb>_cmd.py`

- **Files:** `lib/lore_curator/defrag_curator.py:1046-1112`, `lib/lore_core/lint.py:704-739`, `lib/lore_core/migrate.py:193-237`, `lib/lore_mcp/server.py` (mcp app), `lib/lore_search/cli.py`
- **What:** Phase 1 fenced lower layers from `lore_cli` imports, but typer apps still live in lower layers; `lore_cli/__main__.py` mounts them via `add_typer`. The Phase 1 session log calls this "Phase 1.5, deferred." Each file loses ~30-50 lines of typer boilerplate.
- **LOC:** ~250 lines lift, ~5 new `lore_cli/<verb>_cmd.py` (~150 total). Net ~-100, plus `lore_runtime/argv.py` becomes deletable.
- **Risk:** Medium. CLI smoke test (`lore lint`, `lore migrate`, `lore curator`, `lore mcp`, `lore search`) and ~5 callback-signature tests. Sketch at `docs/ROADMAP-cleanup.md:202-211`.

### 5. Single source for `git config user.email`

- **Files:** `lib/lore_core/session.py:82-94` (`_git_user_email`, the canonical 12-line shape), `lib/lore_curator/session_filer.py:56-70` (inline reimpl with lazy subprocess import + `resolve_handle` chained), `lib/lore_cli/hooks.py:1693-1713` (`_curator_c_email`, with `GIT_AUTHOR_EMAIL` env fallback + `socket.gethostname` fallback)
- **What:** Lift one helper to `lore_core/git.py` (`git_user_email(cwd, *, env_override="GIT_AUTHOR_EMAIL", fallback=None) -> str`). All three call sites converge — `hooks.py` keeps the env-then-hostname fallback by passing kwargs.
- **LOC:** ~45 → ~25.
- **Risk:** Low. Behavioural equivalence is mechanical; `session_filer.py`'s `resolve_handle` post-processing stays at the call site.

### 6. Drop the `LoreBackend` Protocol until a second backend exists

- **Files:** `lib/lore_search/backend.py` (57 LOC, defines `LoreBackend(Protocol)` with one implementer), `lib/lore_search/__init__.py:8-11`, `lib/lore_search/fts.py:29` (only references `SearchHit` from `backend.py`)
- **What:** Every consumer (`lore_mcp/server.py:40`, `lore_core/resume.py:184`, `doctor_cmd.py:156`, `lore_search/cli.py:20`) imports `FtsBackend` directly — nothing types against the Protocol. The aspirational "Qdrant, Chroma" comment ages without a customer. Move `SearchHit` next to `FtsBackend` in `fts.py`; delete `backend.py`. When the second backend lands, re-introduce the Protocol *with* the second implementer in the same patch.
- **LOC:** -57.
- **Risk:** Very low. Keep `SearchHit` exported from `lore_search/__init__.py` so the public API doesn't move.

### 7. Three flock contexts → one helper in `lore_core/lockfile.py`

- **Files:** `lib/lore_core/lockfile.py:34-78` (`try_acquire_spawn_lock`), `lib/lore_core/hook_log.py:91-100` (rotation lock), `lib/lore_core/install/_helpers.py:127-141` (`_flocked` for `json_merge_atomic`)
- **What:** All three are POSIX `fcntl.flock(LOCK_EX[…|LOCK_NB])` on a sibling lockfile, with the same fd-open-then-flock-then-close shape. Lift `flocked(path, *, blocking=True) -> contextmanager` into `lockfile.py`; the three call sites collapse to ~1 line each. The mkdir-based `curator_lock` stays separate (different primitive, different staleness story).
- **LOC:** ~40 → ~25, with the discipline now visible in one place.
- **Risk:** Low-medium. `hook_log.py`'s rotation contract requires `LOCK_NB`; expose the kwarg. The `install/_helpers.py` flock survives `os.replace` by locking a sibling — that's the helper's contract too. Test under `pytest tests/test_hook_log.py tests/test_install_*`.

### 8. Curator A/B/C alias deprecation — schedule a 0.x removal

- **Files:** `lib/lore_curator/__init__.py:27-29`, `lib/lore_curator/session_curator.py`, `lib/lore_curator/daily_curator.py`, `lib/lore_curator/defrag_curator.py`
- **What:** Phase 8 renamed the modules but kept `run_curator_a/b/c` aliases — current usage skews 195:11 toward old names. Aliases that are still preferred 18:1 are *not* aliases; they're the canonical names with extra clutter. Either: (a) flip the prevailing direction — make ~190 grep-mechanical replacements in `lib/` and `tests/`, drop the aliases — or (b) drop the new names and the docstring rename ceremony, restore "A/B/C" as canonical. The triad doc in `__init__.py` is good either way.
- **LOC:** -8 (alias lines + docstring). Bigger payoff is conceptual: stop paying tax for a half-applied rename.
- **Risk:** Medium if you do (a) — large diff, but mechanical. Low if (b). The original Phase 4 audit punted on this; the data now says the rename did not stick.

---

## Modules that could merge

- **`lore_runtime` (301 LOC, 2 files) → fold away** *after* #4 lands. `argv.py` only exists because lower layers expose typer apps; lift those, and it has no consumer outside `lore_cli` — make it private `lore_cli/_argv_compat.py`. `run_render.py` is pure stdlib data and belongs in `lore_core/run_render.py`. The package disappears.

- **`lore_sinks` → `lore_core/briefing/sinks/`** (#2). Aspirational package with an unused Protocol.

- **`lore_search/backend.py` → `lore_search/fts.py`** (#6). Single-implementer Protocol, no typed consumers.

- **`lore_core/install/_helpers.py` (734 LOC) is *not* a wrapper-collector** — it owns `json_merge_atomic`, `install_self_via`, `detect_install_sh_artifacts`, plus the `preview/execute/undo_action` triplet. The 18 `Action.kind` branches could move to per-kind methods, but that's a Visitor with no second visitor. Leave as-is.

---

## Don't simplify (load-bearing for non-obvious reasons)

- **The three "scope" stores** (`lore_core/scopes.py`, `lore_core/state/scopes.py`, `lore_core/state/attachments.py`). Phase 2's `docs/architecture/state.md` explicitly documented them as collaborating, not duplicating. The grumpy review's "five files implementing one mapping" was the wrong frame. Don't merge.

- **`hooks._run_gh` and the 1-line wrappers around `_gh_mod`** (kept in opportunity #3): test `tests/test_hooks_v2.py:299,329` does `monkeypatch.setattr(hooks, "_run_gh", …)`. Inlining `_run_gh` would silently break the test — the patch would land on a free function the production code no longer reads. Keep the local indirection.

- **`tests/conftest.py` autouse `LORE_NOTEWORTHY_MODE=llm_only`**. The docstring is honest about being a grandfather clause, and Phase 6 added `tests/test_curator_a_cascade_default.py` to close the coverage gap. Removing the autouse would invalidate ~hundreds of grandfathered curator tests in one swing.

- **`_DEFRAG_PASSES` registry + lazy `_ensure_passes_registered`** in `defrag_curator.py:631-643`. Looks like a 3-entry registry pattern asking to be inlined. It exists to break a circular import: `c_*.py` files each `from lore_curator.defrag_curator import _DEFRAG_PASSES` to register themselves; importing them eagerly from `defrag_curator` would loop. The lazy import is the simpler of the two valid solutions.

- **`lore_curator/llm_client.py` `LlmClient` Protocol with 3 implementers** (`SDKClient`, `SubprocessClient`, `OpenAICompatibleClient`). Ships under multiple backends today; the Protocol is doing real work and the FakeAnthropic test doubles match its shape too. Don't collapse to a single class.

---

## Calibration note

Phase 6's "decompose `run_curator_c`" claim is honest — function is 145 lines (678–822 in `lib/lore_curator/defrag_curator.py`), down from 237; three named helpers extracted (`_filter_already_ran_this_week`, `_write_defrag_diff_logs`, `_finalize_curator_c_ledger`). The cleanup arc actually delivered.
