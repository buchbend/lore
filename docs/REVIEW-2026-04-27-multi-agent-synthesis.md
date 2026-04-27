# Lore Multi-Agent Review — Synthesis

**Date:** 2026-04-26
**Repo state:** v0.10.2 just shipped after Phases 0–8 cleanup
**Source reports:**
- `/tmp/claude/lore-review-code-quality.md` — code-reviewer
- `/tmp/claude/lore-review-simplify.md` — code-simplifier
- `/tmp/claude/lore-review-architect.md` — senior-architect
- `/tmp/claude/lore-review-ux.md` — ui-ux-designer

This document consolidates the four passes. **Findings flagged by multiple agents bubble to the top** — those are the highest-confidence priorities.

---

## TL;DR

The codebase is in materially good shape post-Phase-8. The four reviews independently converge on **one big risk and three big leverage points**:

1. **Cross-host sync is config-shaped, not implemented.** `WikiConfig.git.auto_pull` and `auto_push` are dataclass fields with zero callers. The README sells "cross-host, cross-team"; the code today is a beautifully-architected single-host tool. **This is the single biggest gap between vision and reality.**
2. **Pluggability for sinks is a half-finished promise.** `BriefingSink` Protocol exists; dispatch is hardcoded `if sink.startswith("markdown:")`. (Architect Risk C / Simplifier #2 — convergent finding.)
3. **The product's actual UX (the SessionStart banner and `lore status`) is invisible in the README.** No screenshot, no diagram, no "first 10 minutes" walkthrough. The drill-down promise works but is undiscoverable mid-session.
4. **Telemetry/code hygiene from the Phase 0–8 sweep has small leftovers** — `no_anthropic_client` skip-reason, an "alive" legacy-cache deprecation, dead branch parity in `run_curator_a`. None critical; all cheap to fix.

---

## Cross-cutting findings (multiple agents)

These are the items where two or more reviewers independently landed on the same problem. Treat as high-confidence priorities.

### CC1 — Sinks Protocol exists but isn't wired
**Flagged by:** Architect (Risk C) + Simplifier (#2)
**Picture:** `BriefingSink` Protocol in `lib/lore_sinks/__init__.py`. Dispatch in `daily_curator.py:440-451` is `if sink.startswith("markdown:")`. CLI `briefing publish` has its own `_KNOWN_SINKS` static set in `briefing_cmd.py:64`. Two parallel half-implementations.
**Resolution that satisfies both:** Build a real registry (architect's Move 2) and put it in `lore_core/briefing/sinks/` (simplifier's location preference) — collapses the orphan top-level `lore_sinks` package while making the Protocol load-bearing. Add setuptools entry-point discovery so `pip install lore-sink-slack` works.

### CC2 — Drill-down works mechanically but is invisible to users
**Flagged by:** Architect ("`lore_search` returns paths but not the wikilink-graph context by default") + UX (#2 — banner says `· consulted [[note]]` but nothing tells the user `/lore:context` exists)
**Resolution:** Two changes. (a) Add a one-line `lore drill <query>` command that chains `search → read → wikilinks` end-to-end. (b) Change the SessionStart banner footer to `· /lore:context to see what loaded · /lore:resume to load more`. The status line is the highest-traffic UI surface; it's where discoverability has to live.

### CC3 — End-to-end "did the note become reachable to the agent?" loop is untested
**Flagged by:** Code-reviewer (test-gap #1) + Architect (Risk B — reindex throttle isn't invalidated on git pull)
**Resolution:** One integration test that runs SessionStart → curator-A → MCP `lore_search`/`lore_read` against a real fixture. Then add the reindex-invalidation hook (touch a `.lore-touch` file or use `watchdog`) and a contention test. This loop is the product's value proposition; it is currently covered only by individually-mocked unit tests on each side.

### CC4 — Curator A/B/C naming is half-applied
**Flagged by:** Simplifier (#8 — usage skews 195:11 toward old names) + UX (vocabulary audit) + maintainer's own memory note (curator-naming feedback memory)
**Resolution:** Pick one canonical and drop the other. Simplifier recommends keeping `A/B/C` since the rename didn't stick, with one consistent user-facing label ("Curator"). The conceptual "session note / per-day surfaces / weekly defrag" triad in `__init__.py` is good; what's churning is the function-name aliasing. Decision is the maintainer's call but the *current* fence-sitting costs every reader.

### CC5 — Layering fence is great; `lore_runtime` is bureaucracy
**Flagged by:** Architect ("Layering fence: Strong — keep it") + Simplifier ("`lore_runtime` (301 LOC, 2 files) → fold away after #4")
**No conflict:** Both reviewers agree the *fence* (the static guard in `tests/test_layering.py`) is load-bearing and should stay. The *package* `lore_runtime` exists as a workaround for typer apps living in lower layers. Lift those (Simplifier #4 = Architect Move 3 = "Phase 1.5 deferred" in roadmap), and `lore_runtime` becomes deletable. The fence rule moves to a `tests/test_layering.py` constant, not a Python package.

---

## Prioritized action list

Items grouped by phase. Each item carries an effort hint and which agent flagged it.

### P0 — Close the vision gap (one phase, ships as 0.11.0)

| # | Action | Agent | Effort |
|---|---|---|---|
| P0.1 | Wire `auto_pull` into SessionStart hook chain (fetch + ff on clean tree, no-op otherwise). Wire `auto_push` into Curator A's post-commit. Define explicit conflict policy in `docs/architecture/sync.md` ADR. | Architect Move 1 / Risk A | ~300 LOC + 150 LOC tests |
| P0.2 | fs-watch reindex invalidation OR a `.lore-touch` mtime-bump file that invalidates the MCP throttle when the wiki repo changes underneath. | Architect Risk B | ~80 LOC + watchdog dep |
| P0.3 | Sink registry mirroring the adapter-registry pattern; move `lore_sinks/` → `lore_core/briefing/sinks/`; setuptools entry-point discovery. | Architect Move 2 + Simplifier #2 | ~150 LOC |

**Rationale for shipping these together:** P0.1 and P0.2 close the pollination loop; P0.3 turns the briefing system from "demo" to "ecosystem." Without P0.1, every multi-host story silently relies on the user remembering `git pull`/`git push`. Without P0.3, "wire my Slack briefing" is a fork-the-repo task.

### P1 — Quick wins (≤ 1 day each, ship continuously)

| # | Action | Agent | Effort |
|---|---|---|---|
| P1.1 | Rename `no_anthropic_client` → `no_llm_client` (constant + 3 test sites). Add to drift guard. | Code-quality #1 | 30 min |
| P1.2 | Stop writing `_legacy_cache_path` on every SessionStart (`hooks.py:996-999`). Keep read-only fallback for one release; then delete. | Code-quality #2 | 30 min |
| P1.3 | Run `ruff --select F401` across `lib/`. Start with `daily_curator.py:4`. | Code-quality #5 | 15 min |
| P1.4 | Consolidate `_split_frontmatter` / inline frontmatter parsing into `lore_core/schema.py` (5 sites → 1). | Simplifier #1 | 1 hour |
| P1.5 | Drop `lore_search/backend.py` (Protocol with one implementer, zero typed consumers). Move `SearchHit` next to `FtsBackend`. | Simplifier #6 | 30 min |
| P1.6 | **README: add a real terminal screenshot of `lore status` and the SessionStart banner under "The pitch", plus one data-flow diagram.** | UX #1, #6 | 1 hour |
| P1.7 | Unify the three install instructions in README (Install / Bootstrap / Marketplace) into one canonical block. | UX #3 | 30 min |
| P1.8 | Document `lore status` in README under Observability — single highest-impact doc edit. | UX #6 | 15 min |
| P1.9 | Fix broken `give-these-considerations-to-melodic-castle.md` link in `CONTRIBUTING.md:175`. | UX | 5 min |

### P2 — Structural simplifications (after P0/P1)

| # | Action | Agent | Effort |
|---|---|---|---|
| P2.1 | Extract `_iterate_pending(...)` from `run_curator_a` to deduplicate dry-run vs non-dry-run loop bodies (`session_curator.py:162-205`). Same shape Phase 6 fixed in `run_curator_c`. | Code-quality #4 | 2 hours |
| P2.2 | Replace `_DEFRAG_PASSES` import-time `_register()` side-effects with explicit registry populated at call time. | Code-quality #3 | 2 hours |
| P2.3 | Single `git_user_email` helper in `lore_core/git.py`; collapse 3 reimplementations. | Simplifier #5 | 1 hour |
| P2.4 | Single `flocked(path, *, blocking=True)` helper in `lore_core/lockfile.py`; collapse 3 fcntl contexts. | Simplifier #7 | 1 hour |
| P2.5 | Inline 3 of 4 `_gh_*` wrappers in `hooks.py:587-608` (only `_run_gh` is monkeypatched in tests). | Simplifier #3 | 30 min |
| P2.6 | Resolve curator A/B/C alias: pick canonical, mechanical rename, drop alias. (See CC4 for decision pressure.) | Simplifier #8 + UX | 4 hours mechanical |
| P2.7 | Stop unconditionally calling `migrate_legacy_pending_breadcrumb()` in `breadcrumb.py:75`. Either gate or delete. | Code-quality (dead path) | 30 min |
| P2.8 | Audit lingering `# legacy — retained during deprecation` comments in `lint.py:368`, `schema.py:24`. Pin to a version or drop the deprecation framing. | Code-quality (dead path) | 15 min |

### P3 — Bigger structural work (a phase each)

| # | Action | Agent | Effort |
|---|---|---|---|
| P3.1 | Lift 5 lower-layer typer apps into `lore_cli/<verb>_cmd.py`. Then delete `lore_runtime` (move `argv` to `lore_cli/_argv_compat.py`, `run_render.py` to `lore_core/`). Document the seam as "Lore subpackage CLI contract" in `docs/architecture/cli-contract.md`. | Simplifier #4 + Architect Move 3 + CC5 | One phase |
| P3.2 | Add `lore drill <query>` end-to-end command (search → read → wikilinks). Surface `/lore:context` and `/lore:resume` affordances in the SessionStart banner footer. | Architect drill-down gap + UX #2 + CC2 | ~150 LOC |
| P3.3 | Slash-command vocabulary cleanup: collapse `/lore:on`/`/lore:off`/`/lore:loud`/`/lore:quiet` to two arg-bearing commands (or one cycler). Rename `/lore:new-wiki` to align with `lore wiki new`. Audit `/lore:surface-init` vs `/lore:surface-add` vs CLI `lore surface add`. | UX #4, #7 | ~1 day |
| P3.4 | Add a "subpackage CLI contract" layering test: `lore_cli/__main__.py` only mounts subapps, doesn't define commands inline. | Architect Move 3 | ~50 LOC |

### P4 — Test coverage (in parallel with P0/P2)

| # | Action | Agent | Effort |
|---|---|---|---|
| P4.1 | **E2E SessionStart → curator-A → MCP `lore_search`/`lore_read` integration test.** Highest-priority gap; the loop the product depends on is uncovered. | Code-quality / Architect (CC3) | ~1 day |
| P4.2 | Test that MCP reindex throttle is invalidated when the underlying wiki repo changes (post-pull, post-touch-file). | Architect Risk B | 2 hours |
| P4.3 | Parametric test that introspects every `handle_*` in `lore_mcp/server.py` and asserts error returns match `_mcp_error` envelope shape. (`handle_surface_context:296-308` was missed in Phase 5.) | Code-quality (test-gap) | 1 hour |
| P4.4 | `run_curator_a` dry-run vs non-dry-run parity test (identical fixture, identical outcomes modulo writes). | Code-quality (test-gap) | 1 hour |
| P4.5 | `_DEFRAG_PASSES` registration unit test (after P2.2 lands). | Code-quality (test-gap) | 1 hour |

### P5 — Documentation (in parallel)

| # | Action | Agent | Effort |
|---|---|---|---|
| P5.1 | `docs/architecture/sync.md` ADR — conflict policy, append-only sessions, surface last-writer-wins. | Architect Risk A | 2 hours |
| P5.2 | `docs/architecture/cli-contract.md` — the "every subpackage ships a `typer.Typer` named `app`" convention. | Architect Move 3 | 1 hour |
| P5.3 | README "first 10 minutes" walkthrough — literal transcript of what the user sees from install to first session note. | UX docs gaps | 4 hours |
| P5.4 | README troubleshooting decision tree (no banner? no vault? no attach? hooks not wired? `lore install` not run?). | UX docs gaps | 2 hours |
| P5.5 | `docs/cost.md` — token/$ table for SessionStart, PreCompact, Curator A/B passes; how to dial cost down. | UX docs gaps | 2 hours |

---

## What's already great (preserve, don't accidentally regress)

These were called out by multiple reviewers as exemplary. Treat as architectural moats:

- **State-file split** (`attachments.json` host-local / `scopes.json` vault-regenerable / `<wiki>/_scopes.yml` portable) — *textbook* application of "what travels with what." `docs/architecture/state.md` is the artifact every project should aspire to. (Architect)
- **Layering fence + static guard** (`tests/test_layering.py`) — real architecture-as-code; survives lazy imports inside functions. The fence concept is load-bearing; only the *package* `lore_runtime` is bureaucracy. (Code-reviewer + Architect)
- **`TranscriptHandle` + content-hash watermarks** — the unsung hero. Survives mid-stream transcript mutation, partial reads, two hosts pointing at one transcript. Without this, drill-down across hosts and adapters is theoretical. (Architect)
- **`atomic_write_text` + flock-guarded append discipline** — consistent across cache, ledger, hook-events, diff-log. The fact that a prior review's "concurrent-write corruption" claim was *debunked* by source-reading is a strong signal. (Code-reviewer)
- **Phase 0–8 audit-then-debunk discipline** — `docs/ROADMAP-cleanup.md` tracks every claim from the original review with DONE / DEFERRED / DEBUNKED status. Three claims debunked. Unusual and disciplined. (Code-reviewer)
- **`require_lore_root()` + typed exceptions + `LoreRootMissing`** — exactly the shape the codebase needed; CLI error handling is uniform. (Code-reviewer)
- **`lore status` design** — activity-first, decay-ordered, loud-on-earning. The 7-line layout is exemplary; should be on the README's first screen. (UX)
- **SessionStart banner shape** — `lore 0.9.0: active · [[wiki]] · last note: …` — genuinely good information density. (UX)
- **`attach_cmd.py` scope-conflict error message** — three explicit ordered next steps. Gold standard for CLI error UX in this codebase; should be the template for every other error path. (UX)

---

## Items the reviewers explicitly say *not* to simplify

(From the simplifier's "Don't simplify" + architect's "what's already great" — calibrates trust in the simplification list.)

- The three "scope" stores — Phase 2 documented the distinct roles; the grumpy review's "five files implementing one mapping" was the wrong frame.
- `hooks._run_gh` — load-bearing test monkeypatch contract.
- `tests/conftest.py` autouse `LORE_NOTEWORTHY_MODE=llm_only` — grandfather clause with explicit migration policy.
- `_DEFRAG_PASSES` lazy registry pattern — circular-import workaround. (Note: the *side-effect registration* in P2.2 is a different fix — replace import-time `_register()` calls with an explicit registry populated at `_ensure_passes_registered()` call time. Keep the lazy import; remove the side effect.)
- `LlmClient` Protocol — three real implementers, FakeAnthropic test doubles match the shape. Doing real work.

---

## One-paragraph executive summary

Lore at v0.10.2 is a well-architected single-host knowledge-vault tool that *describes* a multi-host product. The Phases 0–8 cleanup arc was honest and effective; the codebase has clean state separation, a real layering fence, content-hash transcript watermarks, and exemplary CLI design in `lore status`. **What it lacks is the cross-host plumbing and the user-facing scaffolding that the README pitches.** Wire `auto_pull`/`auto_push` (P0.1), invalidate the MCP reindex on pull (P0.2), turn the sink Protocol into a real registry (P0.3), and put a screenshot of `lore status` on the README's first screen (P1.6) — these four moves convert Lore from a beautifully-architected single-host tool into the cross-host AI–human knowledge vault its vision describes. Everything else on the list is incremental improvement on a sound base.
