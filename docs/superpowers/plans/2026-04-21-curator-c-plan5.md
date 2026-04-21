# Curator C — Weekly Defragmentation (Plan 5)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Each task below is independently committable; `pytest -q` passes after every commit.

**Goal:** Ship Curator C (weekly whole-wiki defragmentation) behind `curator.curator_c.enabled: false`. Fills the third slot of the A/B/C triad. LLM-driven adjacent-concept merge, auto-supersession (proposal-only v1), orphan-wikilink repair, draft-promotion proposals; SessionStart-triggered on ISO-week rollover with per-user 48h jitter; full pre/post diff audit log.

**Architecture:** Extends the existing `lore_curator/curator_c.py` (hygiene passes from Plan A). Adds new LLM-driven passes through a shared candidate-generation + schema-validation harness; wires a weekly SessionStart trigger (reuses Plan A's heartbeat model: time-check + global lock + detached spawn); writes a full-wiki diff log every run for rollback auditability. Ships dark (config-flag-off) per spec §6 — experimental until prompts calibrate on real data.

**Tech Stack:** existing Python + `llm_client` seam from Plan 2.5. No new runtime deps.

**Spec reference:**
- `docs/superpowers/specs/2026-04-19-passive-capture-v1-design.md` §6 (Curator C)
- Memory: `project_curator_triad`, `project_lore_heartbeat`

**Context (what's already built):** Plan A gave C the foundation — `CuratorCConfig` dataclass, `WikiLedger.last_curator_c`, `try_acquire_spawn_lock("c")`, `CaptureState` overdue checks, existing hygiene passes in `curator_c.py`.

**What's missing:** LLM adjacent-merge, auto-supersession detection, orphan repair, draft promotion, weekly trigger, feature-flag wiring, diff-log, `--defrag` CLI, team coordination, high-off degradation.

**Design rules** (added per review):
- **Proposal-only v1.** All LLM-driven passes write *proposal markers* (draft notes, `supersede_candidate:`, `merge_candidate:`, `promotion_candidate:`), never flip end-state frontmatter. Applies to Tasks 7-10. User promotes manually. Exception: orphan-link typo rewrites (Task 9) are typo-class repairs and DO mutate in place — but gated behind a sub-flag `curator.curator_c.defrag_body_writes` (default false).
- **All LLM passes fail-safe.** Malformed response (missing field, wrong type, schema violation) → skip pair, log warning, never mutate vault. Shared parametrized malformed-response fixture in tests.
- **UTC-pinned.** ISO-week math, jitter offsets, `last_curator_c` timestamps — all UTC. No local timezone anywhere.
- **Obsidian-holding check.** Every LLM pass inherits the existing `is_obsidian_holding` guard used by hygiene passes (already in `curator_c.py`).
- **Mid-conflict vault check.** Pre-flight `git status --porcelain` — if the wiki repo has unmerged paths, abort with a clear message and write a noop diff-log entry.
- **"Ships dark" gate** is proven by one end-to-end test: fresh vault with no `.lore-wiki.yml` → zero new files, zero LLM calls, zero frontmatter changes after running every C entry point.

**Non-blockers** (carry forward, don't block ship):
1. `mode: central` — not implemented; config-load accepts the value but fail-loudly on spawn attempt ("central mode deferred to v2"). No branching on it in v1.
2. Prompt calibration through dogfood; v1 ships with baseline thresholds.
3. Body-merge edits (actually rewriting note bodies during adjacent-concept merges) deferred — v1 proposes only.
4. Cross-clone git-push-lag race in team mode — user B runs even after user A pushed because B's clone hasn't pulled yet. Documented cost (~one redundant run per team per week); `mode: central` fixes it in v2.

**Phases:**
- **Phase A — Infrastructure + shared harness** (Tasks 1–5)
- **Phase B — LLM passes** (Tasks 6–9) — each lands inside the already-wired integration
- **Phase C — Polish + coordination** (Tasks 10–12)

TDD per CLAUDE.md. Ships dark: existing installs see zero behavior change.

---

## Phase A — Infrastructure + Shared Harness

### Task 1: Weekly SessionStart trigger with ISO-week + per-user jitter

**Files:** Edit `lib/lore_cli/hooks.py` + test `tests/test_curator_c_trigger.py`

**Logic:**
- UTC-only. `iso_week_now = datetime.now(UTC).isocalendar().week`; compare to `iso_week(last_curator_c)`.
- Jitter: `offset_seconds = int(hashlib.sha256(email.encode()).hexdigest()[:8], 16) % 172800` (0-48h). Trigger fires only if `now_utc >= monday_00_utc + offset_seconds`.
- **Email fallback:** if `git config user.email` is unset/empty, use `socket.gethostname()` as the hash input; if that fails too, offset=0 (Monday 00:00Z fire).
- Feature gate: `curator.curator_c.enabled==True` required; `mode!="local"` → fail-loudly log and skip (no branching).
- Reuses Plan A's `try_acquire_spawn_lock(lore_root, "c")`.

**Acceptance:**
- `test_trigger_fires_on_new_iso_week` — last_curator_c from prior week → spawn.
- `test_trigger_skips_same_iso_week` — current-week ledger → no spawn.
- `test_trigger_respects_jitter_window` — fixture email `"fixture@test"` with computed offset hardcoded in the docstring (document WHY 04:00Z fires); at 02:00Z no spawn, at 05:00Z yes.
- `test_trigger_jitter_fallback_when_git_email_unset` — unset email uses hostname; still deterministic.
- `test_trigger_fires_exactly_once_across_monday_boundary` — Sunday 23:59Z no, Monday 00:01Z+offset yes. UTC-pinned.
- `test_trigger_disabled_by_default` — fresh config → no spawn.
- `test_trigger_central_mode_fails_loudly` — `mode=central` → no spawn, emits warning log, does NOT silently skip.
- `test_trigger_concurrent_sessions_coordinate` — multiprocess Barrier(4); exactly one spawns (flock regression guard).

**Commit:** `feat(curator): weekly SessionStart trigger for Curator C (UTC + user jitter)`

---

### Task 2: "Ships-dark" end-to-end gate test

**Files:** `tests/test_c_ships_dark_gate.py` (new; no code changes)

**Rationale:** Merge gate. Proves fresh vault with no explicit `.lore-wiki.yml` gets zero new behavior post-Plan-5.

**The test:** seed a minimal vault (no `.lore-wiki.yml`), simulate a week of activity — run SessionStart hooks, bare `lore curator run`, `lore curator run --defrag` without `--force`, `lore status`, `lore doctor`. Snapshot `.lore/`, `wiki/`, every note's frontmatter + body, every cache file. Assert **exact byte-for-byte equality** after vs. before for the whole vault tree except explicitly-allowed paths (`hook-events.jsonl`, `.lore/runs/`).

**Acceptance:**
- `test_fresh_vault_no_llm_calls` — mock `make_llm_client` to raise if called; run the full C surface; assert zero calls.
- `test_fresh_vault_no_spawns` — mock `_spawn_detached_curator_c`; assert zero calls across a SessionStart loop.
- `test_fresh_vault_no_frontmatter_mutations` — note frontmatter byte-equal after.
- `test_fresh_vault_no_diff_log` — no `.lore/curator-c.diff.*.log` file created.
- `test_fresh_vault_no_new_config_files` — no `.lore-wiki.yml` appears from default-write.

**Commit:** `test(curator): ships-dark gate — fresh vault sees zero C behavior`

---

### Task 3: Pre/post-diff audit log

**Files:** Create `lib/lore_curator/curator_c_diff.py` + test `tests/test_curator_c_diff.py`

**Output path:** `<lore_root>/.lore/curator-c.diff.YYYY-MM-DD.log`. Each entry carries `run_id` (ties to `runs/<id>.jsonl`). 90-day retention — runs pass prunes logs older than 90d. Size cap: 10 MB per log (rotates to `.log.1`).

**Zero-change runs:** write a single-line marker `<timestamp> run=<id> status=no-op\n`, not a full empty entry. Audit log stays grep-clean.

**Permission-denied:** wraps writes in `try/OSError` — on failure, emit a `warning` event to `hook-events.jsonl` (observable via CaptureState), do NOT crash the run.

**Acceptance:**
- `test_diff_log_captures_frontmatter_changes`
- `test_diff_log_dry_run_writes_entry` — dry-run header prefixed.
- `test_diff_log_daily_append` — two runs same day → two entries, both with distinct `run_id`.
- `test_diff_log_no_op_writes_single_line_marker`
- `test_diff_log_90d_retention` — pre-seed 100d-old log → deleted on next run.
- `test_diff_log_10mb_rotation` — synthetic 11 MB log → rotates to `.1`.
- `test_diff_log_permission_denied_emits_warning_event` — chmod'd dir → warning in hook-events, no crash.

**Commit:** `feat(curator): pre/post-diff audit log with run_id + retention + rotation`

---

### Task 4: `lore curator run --defrag [--dry-run]` CLI

**Files:** Edit `lib/lore_curator/curator_c.py::run_command` + test `tests/test_curator_c_cli.py`

- `--defrag` flag passes through to `run_curator_c(defrag=True)`.
- `--dry-run` honored across both legacy hygiene and new LLM passes.
- Prints a summary table matching the diff-log summary.

**Acceptance:**
- `test_defrag_flag_invokes_llm_passes` (once Task 5+ lands; noop-pass placeholder now)
- `test_defrag_dry_run_writes_no_notes`
- `test_defrag_without_flag_keeps_legacy_behaviour`
- `test_defrag_without_llm_client_skips_with_clear_log_and_returns_skipped_status` — run without LLM → `CuratorReport.status == "skipped_no_llm"`, summary table shows "skipped (no LLM)".

**Commit:** `feat(cli): lore curator run --defrag [--dry-run]`

---

### Task 5: Integration skeleton + shared pass harness

**Files:** Edit `lib/lore_curator/curator_c.py::run_curator_c` + create `lib/lore_curator/c_passes.py` (shared candidate-gen + schema-validation helpers) + tests

**Architect concern addressed:** Integration exists BEFORE LLM passes land. Each pass in Phase B slots into a working pipeline.

**What lands in this task:**
1. `run_curator_c(*, defrag: bool = False, anthropic_client: LlmClient | None = None, dry_run: bool = False)` signature extended with the defrag/client seam.
2. Pass-list registry: an empty list in this task. Phase B appends each pass as a callable.
3. Diff-log wrapping of the whole run (snapshot → run passes → snapshot → write log).
4. `WikiLedger.update_last_curator("c")` atomic-on-success update.
5. Mid-merge guard: `git status --porcelain` check pre-run; on conflict → noop diff-log + abort.
6. Obsidian-holding guard: reuse existing `is_obsidian_holding` — any pass that would mutate skips with a first-run warning.
7. Shared harness in `c_passes.py`:
   - `validate_llm_response(response, schema: dict) -> dict | None` — returns None on any violation (missing field, wrong type, out-of-range confidence), emits a warning event.
   - `ProposalOnlyError` — raised by a runtime guard around mutation attempts on existing notes during proposal-only passes (keeps convention enforceable).

**Acceptance:**
- `test_integration_skeleton_runs_with_zero_passes` — fresh vault, `defrag=True`, no passes registered → runs cleanly, writes noop diff log, updates `last_curator_c`.
- `test_integration_defrag_false_skips_llm_harness` — no LLM instantiation.
- `test_integration_git_conflict_aborts` — vault mid-rebase → noop diff log, no frontmatter changes.
- `test_integration_obsidian_holding_skips_mutation_passes` — mock `is_obsidian_holding=True` → passes that would mutate log skip-reason.
- `test_integration_last_curator_c_atomic_on_failure` — mid-run exception → `last_curator_c` unchanged (Plan A atomic-or-unchanged).
- `test_validate_llm_response_table` — parametrized: valid / missing field / wrong type / confidence > 1 / confidence < 0 / null / empty → validator returns dict or None with warning.
- `test_proposal_only_guard_blocks_frontmatter_write_to_existing_note` — any pass that tries to mutate a pre-existing note during a proposal-only phase raises.

**Commit:** `feat(curator): Curator C integration skeleton + shared LLM pass harness`

---

## Phase B — LLM Passes (each lands inside the working skeleton)

### Task 6: Adjacent-concept merge (proposal)

**Files:** `lib/lore_curator/c_adjacent_merge.py` + test

**Design:**
- Candidate-pair generation is testable: `generate_merge_candidates(notes) -> Iterable[NotePair]` — pre-filter by fuzzy title/tag ratio (rapidfuzz >= 0.6) + scope overlap. Tests exercise the generator separately.
- LLM tool-call returns `{should_merge: bool, merged_note: {...}, confidence: float}`.
- Threshold `confidence >= 0.8`. Document `>=` inclusive.
- Writes new draft note with `draft: true`, `merge_candidate_sources: [[a]], [[b]]`. Does NOT edit originals (guarded by `ProposalOnlyError` from Task 5).

**Acceptance:**
- `test_adjacent_merge_candidate_generator_filters_low_overlap` — generator covered, not just fixture pairs.
- `test_adjacent_merge_proposes_new_note_on_0.9_confidence`
- `test_adjacent_merge_proposes_at_exact_0.8` — boundary inclusive.
- `test_adjacent_merge_skips_at_0.79_confidence`
- `test_adjacent_merge_skips_malformed_llm_response` — uses shared malformed-response fixture from Task 5.
- `test_adjacent_merge_idempotent_same_day` — second invocation same day does NOT create duplicate draft (sources-based dedupe).
- `test_adjacent_merge_filename_collision_resolved` — proposed slug already exists → suffix with run_id short.
- `test_adjacent_merge_never_edits_originals` (ProposalOnlyError guard).
- `test_adjacent_merge_disk_full_aborts_atomic` — monkeypatched `write_text` raises OSError → run aborts cleanly, no partial writes.
- `test_adjacent_merge_skipped_without_llm`.

**Commit:** `feat(curator): adjacent-concept merge — proposal-only`

---

### Task 7: Auto-supersession (proposal-only via marker)

**Files:** `lib/lore_curator/c_auto_supersede.py` + test

**Design change from v1 draft** (architect must-fix): flipped to proposal-only. Writes `supersede_candidate: [[newer]]` to the older note's frontmatter AND `supersede_candidate_of: [[older]]` to the newer. User promotes manually (flip `supersede_candidate` → `superseded_by`).

- Candidate pairs: same `type: decision`, overlapping scope, newer `created:` date.
- LLM tool-call: `{contradicts: bool, confidence: float, reason: str}`.
- Conservative: `contradicts AND confidence >= 0.85 AND not canonical`. Note-level `canonical: true` only in v1 (wiki-level canonical scopes documented as deferred).
- Circular-supersession guard: if a candidate chain would create a cycle, skip with warning.

**Acceptance:**
- `test_supersede_proposes_on_0.9_confidence` — both notes get `supersede_candidate_*` markers, no `superseded_by` flip.
- `test_supersede_proposes_at_exact_0.85` (boundary inclusive).
- `test_supersede_skips_at_0.84`.
- `test_supersede_skips_canonical_true`.
- `test_supersede_ignores_wiki_level_canonical_in_v1` — documented deferral.
- `test_supersede_circular_chain_guard` — explicit `supersedes:` chain already A→B → skip proposed B→A.
- `test_supersede_skips_malformed_llm_response` (shared fixture).
- `test_supersede_idempotent_same_day` — re-run sees markers, skips.
- `test_supersede_only_proposes_never_flips_superseded_by`.
- `test_supersede_skipped_without_llm`.

**Commit:** `feat(curator): auto-supersession proposal (supersede_candidate marker, no frontmatter flip)`

---

### Task 8: Orphan wikilink repair (in-place, behind sub-flag)

**Files:** `lib/lore_curator/c_orphan_links.py` + test

**Design (code-reviewer must-fix):** Body mutation has higher blast radius than frontmatter. Gated behind sub-flag `curator.curator_c.defrag_body_writes: false` (default false). With sub-flag off → proposes rewrites in a separate `<lore_root>/.lore/curator-c.body-proposals.YYYY-MM-DD.log`. With sub-flag on → mutates in place.

- Orphan detection: `[[slug]]` → no matching note file.
- Fuzzy-match top candidate by slug ratio (>= 0.7).
- LLM tool-call: `{is_rename: bool, canonical_slug: str, confidence: float}`. Confidence >= 0.8 required.
- Preserves display text `[[slug|Display]]`.
- Preserves surrounding whitespace and line endings byte-for-byte.

**Acceptance:**
- `test_orphan_with_sub_flag_off_writes_proposal_log_only` — body bytes unchanged.
- `test_orphan_with_sub_flag_on_rewrites_link_in_place`.
- `test_orphan_preserves_display_text`.
- `test_orphan_preserves_crlf_and_trailing_newline` — fixture with CRLF line endings → preserved exactly.
- `test_orphan_ambiguous_candidates_no_rewrite` — two 0.85-ratio candidates → skip + log.
- `test_orphan_deleted_target_no_fuzzy_match_is_flagged`.
- `test_orphan_round_trip_frontmatter_structural_equivalence` — after rewrite, YAML re-parses to structurally equal dict (catches quoting regressions).
- `test_orphan_skips_malformed_llm_response` (shared fixture).
- `test_orphan_skipped_without_llm`.

**Commit:** `feat(curator): orphan wikilink repair — sub-flag gated body rewrites`

---

### Task 9: Draft promotion proposals (time-based, no LLM)

**Files:** fold into `lib/lore_curator/curator_c.py` as a `_pass_draft_promotion` alongside existing `_pass_*` helpers. No standalone module — merciless was right that this is ~30 lines.

**Design:**
- `draft: true` AND `now_utc.date() - created_date > 14d` AND last-edit-stale → write `promotion_candidate: true` marker. Never flips `draft: false`.
- No LLM; pure frontmatter.

**Acceptance:**
- `test_promotion_proposes_on_14d_plus_draft`.
- `test_promotion_skips_recent_drafts_below_14d`.
- `test_promotion_skips_at_exact_14d` (boundary exclusive).
- `test_promotion_never_flips_draft_false`.
- `test_promotion_skips_notes_without_draft_frontmatter`.
- `test_promotion_idempotent` — re-run with marker present → no duplicate write.

**Commit:** `feat(curator): draft-promotion proposal pass (time-based)`

---

## Phase C — Polish + Coordination

### Task 10: `models.high: off` degradation warning

**Files:** Edit pass modules + test

**Design simplification from v1 draft** (merciless + code-reviewer): drop the once-per-lore_root sentinel — just emit a warning event to hook-events.jsonl on every run when `high:off`. Much simpler, no sentinel file I/O, still observable via CaptureState (`simple_tier_fallback_active`).

- Tier resolution: `high:off` → merge/supersede passes use `middle`; orphan/promotion unaffected per spec.
- Warning shape: `event="curator-c", outcome="high-tier-off", message="Curator C running without high-tier — adjacent-merge + supersession coarser"`.

**Acceptance:**
- `test_high_off_uses_middle_tier_for_merge_and_supersede`.
- `test_high_off_emits_warning_event_every_run` (not once).
- `test_high_enabled_uses_high_tier`.
- `test_orphan_and_promotion_unaffected_by_high_off`.

**Commit:** `feat(curator): Curator C degrades to middle tier when high:off`

---

### Task 11: First-come-wins coordination

**Files:** Edit `lib/lore_cli/hooks.py::_spawn_detached_curator_c` + `run_curator_c` + test

**Design (merciless clock-skew fix):** Use ISO-week equivalence, not timestamp comparison. `iso_week(last_curator_c) == iso_week(now_utc)` → skip. Avoids clock-skew false-positives across team members.

- Fsync on `WikiLedger.write` (add to Plan A's `ledger.py` if absent) so concurrent reads see committed state.
- Post-lock re-read: within the spawn-lock, after pre-run checks, re-read `last_curator_c`. If another run landed during lock-wait → abort with `"already ran this ISO week by <author> at <ts>"`.

**Acceptance:**
- `test_second_user_skips_after_first_iso_week_match`.
- `test_post_lock_reread_detects_race` — monkeypatch ledger write during lock-hold → run detects and aborts.
- `test_ledger_write_is_fsynced` — mock `os.fsync`; assert called on write path.
- `test_coordination_clock_skew_safe` — user B's clock +2min ahead; iso-week check immune.
- `test_cross_clone_stale_race_documented` — documents (not prevents) the git-pull-lag race; asserts no crash and a clear log line.

**Commit:** `feat(curator): first-come-wins coordination (ISO-week equivalence + fsync)`

---

### Task 12: Final integration assertions

**Files:** `tests/test_curator_c_integration.py` (extended)

- Assert pass execution order: hygiene (staleness / explicit-supersede / implements / backfill) → adjacent-merge → auto-supersede → orphan → draft-promotion.
- Assert Task 2's ships-dark gate still passes after all Phase B/C changes.
- Assert diff-log captures all proposal + mutation types.
- Assert `last_curator_c` only updates on complete success.

**Commit:** `test(curator): full Curator C integration assertions`

---

## Execution notes

- **Order:** 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12. Tasks 1–5 are infrastructure; 6–9 LLM passes can be reordered among themselves; 10–12 are polish.
- **Prerequisite inside Task 5:** extend `FakeAnthropicClient` (from `tests/test_curator_a.py`) to accept arbitrary tool names — confirm the fake is already keyed by tool name; if not, add to Task 5's file list.
- **Shared malformed-response fixture** lives in `tests/conftest.py` or `tests/_fixtures.py` — parametrized across Tasks 6/7/8.
- **Test budget:** ~55 new tests.

## Success criteria

1. Every LLM pass is proposal-only by default (markers, not frontmatter flips). Only orphan-repair with explicit sub-flag mutates bodies.
2. Zero behavior in vaults with default config (Task 2's ships-dark gate passes).
3. Pre/post diff log written per run with run_id + 90d retention.
4. Trigger fires at most once per ISO-week per vault (local concurrency coordinated; cross-clone race documented).
5. UTC-pinned everything.
6. Malformed LLM responses never mutate the vault.

## Review-pass log

Plan drafted 2026-04-21, reviewed by architect + code-reviewer + merciless-dev. Must-fix items folded:

- **Architect:** (1) Phase reorder — integration skeleton (Task 5) lands BEFORE LLM passes. (2) Task 6 (supersession) flipped from frontmatter-flip to `supersede_candidate:` marker — proposal-only symmetry with Task 5's design.
- **Code-reviewer:** (1) Added end-to-end "ships dark" gate as Task 2 — the merge gate. (2) Git-email-unset fallback (hostname → offset=0). (3) ISO-week + TZ pinned UTC. (4) Threshold-boundary (`==`) tests added. (5) Shared malformed-LLM-response fixture across passes. (6) Disk-full / atomic-abort per pass. (7) Orphan sub-flag `defrag_body_writes` default false — gates body rewrites. (8) fsync on ledger write. (9) Round-trip structural-equivalence on orphan rewrites.
- **Merciless:** (1) Central-mode → fail-loudly, no branching. (2) Once-per-lore_root warning sentinel dropped — emit every run. (3) Task 8 draft-promotion folded as a `_pass_` helper inside curator_c.py. (4) `ProposalOnlyError` guard enforces proposal-only convention. (5) Candidate-generator for adjacent-merge is testable separately, not handwaved. (6) Obsidian-holding and mid-merge-vault pre-flight checks explicit.
