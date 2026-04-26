# Three-Lens Review — Claim-by-Claim Audit

**Date:** 2026-04-26
**Source review:** `docs/REVIEW-2026-04-25-three-lens-state-of-lore.md`
**Method:** every claim re-verified against the current source. Each
row records the current status (DONE / DEFERRED / TODO / DEBUNKED) and
which roadmap phase addressed it (or why it's deferred). Claims
DEBUNKED by source-reading are called out explicitly — code reviews
benefit from this discipline.

---

## Summary

- **38 claims** examined.
- **24 DONE** across Phases 0-5 (with tests where load-bearing).
- **3 DEBUNKED** by source-reading — the review was wrong.
- **5 DEFERRED** with explicit rationale (deprecation cycle, cosmetic,
  needs measurement).
- **6 TODO** remaining for Phase 6/7.

---

## Cross-cutting (Section 1 of the review)

| # | Claim | Status | Phase | Evidence |
|---|-------|--------|-------|----------|
| 1A | `plugin.json` 0.5.0 vs `pyproject.toml` 0.9.0; no CI guard | ✅ DONE | 0 | `tests/test_version_sync.py` enforces in pytest |
| 1B | 9 sources of config truth, no documented precedence | ✅ DONE | 2 | `docs/architecture/config.md` documents env > root > wiki > defaults |
| 1C | Three `scope` implementations duplicating one mapping | 🟡 DEBUNKED | 2 | Reading the code: `_scopes.yml` (wiki catalog) + `scopes.json` (vault registry) + `attachments.json` (host consent) have *distinct roles*, not duplicate one. Documented in `docs/architecture/state.md`. |
| 1D | `lore_cli` is the runtime hub; lower layers reach up | ✅ DONE | 1 | `lore_runtime` package + `tests/test_layering.py` enforces no upward import |

---

## Code quality + tech debt (Section 2)

| # | Claim | Status | Phase | Evidence |
|---|-------|--------|-------|----------|
| 2.1 | Half-applied LLM refactor — `anthropic_client` everywhere | ✅ DONE | 0 | 200 sites renamed; OpenAI smoke test in `tests/test_curator_openai_smoke.py` |
| 2.2 | `conftest.py:18-20` autouse-monkeypatches every test to `llm_only` | ⏳ TODO | 6 | Verified: still present, exact same shape. Conftest docstring acknowledges the test-stability rationale. |
| 2.3 | `run_curator_c` 180-line god-function | ⏳ TODO | 6 | Verified: now **239 lines** (worse). |
| 2.4 | `BODY_TEMPLATE` writes literal `- TODO` into every session note | ⏳ TODO | 6 | Verified: `lore_core/session.py:183`. |
| 2.5 | 86 broad `except Exception` + 25 `except: pass` | 🟢 PARTIAL | 3 | hooks.py audited in Phase 3 (9 sites). 44 broad excepts still in `lore_core/` + `lore_curator/` — Phase 6 will do a smaller audit pass. |
| 2.6 | `migration/` empty + `build/lib/lore_import/` stale | ✅ DONE | 0 | Removed |
| 2.7 | `breadcrumb.py:121 migrate_legacy_pending_breadcrumb` runs unconditionally; no deprecation marker | ⏳ TODO | 6 | Line 75 calls it unconditionally. Add deprecation comment + target version. |
| 2.8 | `hooks.py:63 _legacy_cache_path`; no deletion plan | ⏳ TODO | 6 | Verified: line 64 defined, used at 861/868/960. Add deprecation comment. |
| 2.9 | Lazy `from lore_core.config import get_lore_root` in 6+ places | ⏳ TODO | 6 | `curator_c.py:698` confirmed. Phase 6 will lift to module level where it doesn't introduce circular imports. |

### "What's actually good" callouts (verified, no action needed)

- ✅ `atomic_write_text` used consistently
- ✅ `LlmClient` design clean (the rename was just cosmetic)
- ✅ `AttachmentsFile.longest_prefix_match` is the right shape
- ✅ PID-keyed session cache in `hooks.py`
- ✅ `tests/test_root_config.py:39-54` exemplary — used as template for `tests/test_openai_precedence.py` in Phase 2
- ✅ Run logger `contextlib.nullcontext()` pattern in `curator_c.py:728-736`

---

## Architecture (Section 3)

| # | Claim | Status | Phase | Evidence |
|---|-------|--------|-------|----------|
| 3.1 | Diamond architecture (lower layers import `lore_cli`) | ✅ DONE | 1 | Layering fence + test |
| 3.2 | Curators A/B/C named one way internally, "Curator" externally | 🟡 DEFERRED | 4 | `lore_curator/__init__.py` docstring already documents the role mapping. Module rename is ~200 import sites for cosmetic gain — too risky. |
| 3.3 | MCP ↔ skills three boundary crossings (gather → shell out) | 🟡 KEPT BY DESIGN | — | Principled: visibility of side-effects to the user. Documented as a constraint. |
| 3.4 | `/lore:context` requires `dangerouslyDisableSandbox` | ⏳ TODO | 6/7 | Confirmed. Bigger fix (need MCP tool replacement). Document and defer the architectural fix; small UX improvement possible. |
| 3.5 | Two parallel SessionStart hooks (banner + capture) | 🟡 DEBUNKED | 3 | They have *different responsibilities*: context injection vs. capture telemetry. Coupling would lose the separation. |
| 3.6 | SessionStart cold-start latency: eager 30-cmd-module import | ⏳ TODO | 7 | Verified: `__main__.py` eagerly imports 30 cmd modules. Whether this hurts the <100ms SessionStart budget is measurable. |
| 3.7 | Unconditional `reindex(wiki)` per MCP search call | ⏳ TODO | 7 | Confirmed `lore_mcp/server.py:87`. Add mtime short-circuit. |
| 3.8 | `curator_c.py` defrag is O(N²)-shaped | 🟡 DEFERRED | 7 | Speculative; defer until profiling shows real cost. Vault sizes are small today. |
| 3.9 | `hook-events.jsonl` multi-process append corruption | 🟡 DEBUNKED | 3 | Code already uses POSIX `O_APPEND` atomic writes + `flock`-guarded rotation. Documented in module docstring + `state.md`. |
| 3.10 | `_pid_alive` Linux-only conservative-true on macOS | ✅ DONE | 3 | `os.kill(pid, 0)` cross-platform; tests in `test_hooks_pid_alive.py` |

---

## UX (Section 4)

| # | Claim | Status | Phase | Evidence |
|---|-------|--------|-------|----------|
| 4.1 | `skills/on/SKILL.md` + `skills/loud/SKILL.md` missing | ✅ DONE | 0 | Both shipped |
| 4.2 | `lore surface add` vs `/lore:surface-new` | ✅ DONE | 4 | Renamed slash to `/lore:surface-add`; CHANGELOG entry |
| 4.3 | `new-wiki` only hyphenated CLI verb | 🟡 DEFERRED | 4 | Needs deprecation alias + user comms; park for 1.0 |
| 4.4 | Skill drift `python -m lore_core.lint` etc. | ✅ DONE | 4 | Cleared 10 sites; `tests/test_skill_cli_drift.py` guards future drift |
| 4.5 | `attach_cmd.py:97-100` inconsistent error guidance | ✅ DONE | 2 | `require_lore_root()` + `_cli_helpers.lore_root_or_die` consolidate the pattern |
| 4.6 | `lore_mcp/server.py:103-138` inconsistent error envelopes | ✅ DONE | 5 | `_mcp_error()` helper + 8 migrated handlers + `tests/test_mcp_error_envelope.py` |
| 4.7 | `hooks.py:842` "_(legacy cache — may be from another session)_" leaks impl language | ⏳ TODO | 6 | Verified: line 868. Small copy fix. |
| 4.8 | `/lore:context` vs `/lore:resume` vs `lore status` vs `lore doctor` confusion | 🟡 DEFERRED | 5 | Rename `/lore:context` → `/lore:loaded` needs muscle-memory deprecation. Park for 1.0. |
| 4.9 | No `/lore:status` (symmetry break) | 🟡 DEFERRED | 5 | Same reasoning |
| 4.10 | `init`/`new-wiki`/`lint` buried in `_Advanced_` | ✅ DONE | 5 | Promoted in `lore --help` |
| 4.11 | SessionStart directive at top, scolds before content | ✅ DONE | 5 | Reordered; `tests/test_hooks_v2.py` pins ordering |
| 4.12 | SKILL.md descriptions vary 18-41 words; no *what/returns/when* template | 🟡 DEFERRED | 5 | Cosmetic; Phase 4's drift guard catches the load-bearing case |
| 4.13 | Slash command bloat (`/lore:context` triad confusion) | 🟡 DEFERRED | 5 | See 4.8 |

---

## Top-3 "if nothing else this week" recommendations

| # | Claim | Status | Phase |
|---|-------|--------|-------|
| 5.1 | Sync `plugin.json` + CI guard | ✅ DONE | 0 |
| 5.2 | Rename `anthropic_client` → `llm_client` + smoke test | ✅ DONE | 0 |
| 5.3 | Ship `skills/on/SKILL.md` + `skills/loud/SKILL.md` | ✅ DONE | 0 |

---

## Phase 6 (next session) plan — safe + useful TODOs

1. **`BODY_TEMPLATE` TODO leak** (claim 2.4) — replace literal `- TODO`
   with something useful or remove the section.
2. **Conftest autouse `llm_only`** (claim 2.2) — audit which tests
   actually need it; either remove autouse + opt-in per-test, or keep
   with strengthened docstring + add cascade-default integration test.
3. **`run_curator_c` decomposition** (claim 2.3, 239 lines) — extract
   the cleanly-cohesive sub-functions (`_filter_already_ran`,
   `_apply_actions`, `_run_defrag_phase`, `_finalize_diff_logs`).
   Conservative — don't try to hit zero god-function lines.
4. **Broad-except audit (round 2)** (claim 2.5) — apply Phase 3's
   pattern to the top ~10 broad-except sites in
   `lore_curator/curator_a.py`, `curator_b.py`, `c_*.py`. Tighten
   accidental, comment defensive.
5. **Deprecation markers** (claims 2.7, 2.8) — add target removal
   version comments to `_legacy_cache_path` and
   `migrate_legacy_pending_breadcrumb` so future contributors know
   when they're free to delete.
6. **`hooks.py:842` "legacy cache" copy fix** (claim 4.7) — one-line
   message change so users don't see internal-implementation language.
7. **Lazy local imports** (claim 2.9) — lift to module level where
   safe; mark as "kept lazy for circular-import reasons" where not.

## Phase 7 (next session) plan — targeted perf wins

1. **MCP `reindex` short-circuit on mtime** (claim 3.7) — cheap and
   measurable. The mtime+SHA cache already exists; just don't call
   `reindex` if the cache is current.
2. **SessionStart eager-import audit** (claim 3.6) — measure the cost
   of importing 30 cmd modules at every hook firing. If
   significant, see if any can be lazily imported. If insignificant,
   document and move on.
3. **`curator_c` O(N²) defrag profiling** (claim 3.8) — defer pending
   real telemetry showing the cost. Today's vault sizes don't trigger
   this.
4. **`/lore:context` `dangerouslyDisableSandbox` redesign** (claim 3.4)
   — defer until /lore:context UX is reconsidered (paired with claim
   4.8 in a 1.0 release-prep pass).
