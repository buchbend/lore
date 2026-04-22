# Local Lore state — implementation plan

**Date:** 2026-04-22
**Issue:** buchbend/lore#22
**Concept:** `[[local-lore-state]]` in private wiki
**Supersedes:** `[[claude-md-as-scope-anchor]]` model

## Goal

Replace the distributed `## Lore`-in-CLAUDE.md routing model with:

- **`.lore.yml`** — optional, checked-in repo offer.
- **`$LORE_ROOT/.lore/attachments.json`** — machine-local truth, which paths route where.
- **`$LORE_ROOT/.lore/scopes.json`** — the scope tree, flat ID-as-path, regenerable from offers.
- **CLAUDE.md** — dev's personal context; Lore no longer reads or writes it for routing.

No filesystem walk-up. Resolution is longest-prefix match on `attachments.json`.

## Constraints

- **No behavior changes to already-filed session notes.** They carry `scope:` and `wiki:` (via path) already; the new resolver must produce equivalent outputs for all currently-attached paths.
- **Migration is one-shot + lazy fallback.** Users never have to hand-edit anything.
- **Back-compat resolver** reads legacy `## Lore` blocks for one session, then auto-migrates. After Phase 6 the back-compat path is removed.
- **Lockfile discipline unchanged.** All state writes go through `atomic_write_text`. No new locks — the curator lock already serialises the write path; state reads are optimistic.
- **Layer rules honoured.** Parsing of `.lore.yml` lives in `lore_core/` (same layer as the current `lore_core/attach.py`). CLI commands in `lore_cli/`. Curator reads state through `lore_core/` interfaces only.

## Phases

### Phase 1 — State modules + resolver

**New modules** (all in `lib/lore_core/`):

- `state/attachments.py` — `AttachmentsFile` class: load, save, `longest_prefix_match(path) → entry | None`, `add`, `remove`, `decline`, `fingerprint_of(dict) → str`.
- `state/scopes.py` — `ScopesFile` class: load, save, `get(scope_id)`, `ingest_chain(scope_id, wiki)` (backfills parents), `resolve_wiki(scope_id)` (walks ID segments upward until a `wiki:` is found), `descendants(scope_id)`, `rename(old, new)`, `reparent(scope_id, new_parent)`.
- `state/__init__.py` — shared types: `Attachment`, `ScopeEntry`, `Offer`.

**Replace** the walk-up in `lib/lore_core/scope_resolver.py` with a registry-backed version:

```python
def resolve_scope(cwd: Path, attachments: AttachmentsFile) -> Scope | None:
    entry = attachments.longest_prefix_match(cwd)
    if entry is None:
        return None
    return Scope(wiki=entry.wiki, scope=entry.scope_id)
```

Keep the old walk-up under a `_legacy_walk_up_resolve(cwd)` private function for Phase 5 back-compat only.

**Wiring:** pass a single `AttachmentsFile` instance through `run_curator_a` rather than re-loading per call. The ledger's `_resolve_wiki_cached` cache is then redundant — delete it and the `_bucket_for` helper now calls `attachments.longest_prefix_match` directly.

**Tests (new):**
- `test_attachments_file.py` — CRUD, fingerprint determinism, longest-prefix correctness (exact match, nested paths, no match, trailing-slash normalisation, symlink resolution).
- `test_scopes_file.py` — `ingest_chain` idempotence, `resolve_wiki` inheritance, `rename`/`reparent` propagation, descendants query.
- `test_scope_resolver.py` — rewrite existing tests to use `AttachmentsFile` instead of CLAUDE.md fixtures.

**Tests (existing, modified):**
- `test_curator_a.py` — fixtures stop creating CLAUDE.md; create an `attachments.json` instead. Behaviour assertions unchanged.
- `test_ledger.py` — `pending_by_wiki` test fixtures switch to attachments-backed resolution.

**Acceptance:** all green with the new resolver; the `_legacy_walk_up_resolve` is present but never called in the curator pipeline.

---

### Phase 2 — `.lore.yml` parser + consent flow

**New modules:**

- `lore_core/offer.py` — `parse_lore_yml(path: Path) → Offer | None`, `fingerprint(offer: Offer) → str`. Fingerprint covers only routing-relevant fields (`wiki`, `scope`, `wiki_source`); comments and key ordering don't affect it.
- `lore_core/consent.py` — `classify_state(cwd, attachments) → ConsentState`. Returns one of `untracked`, `offered`, `attached`, `dormant`, `manual`, `drift`.

**SessionStart hook update** (`lib/lore_cli/hooks.py`):

- On session start, resolve cwd → `ConsentState`.
- If `offered` or `drift`, and the session is interactive (TTY check), emit a one-line prompt in the SessionStart output frame: *"This repo offers attachment to wiki `X`. Attach? (run `/lore:attach` to accept, `/lore:attach --decline` to dismiss)"*. Do NOT block.
- If non-interactive, log `.lore-yml-offered` to hook events but stay silent on output.

**No new interactive prompts at the adapter level.** The SessionStart emission is just text; the user types a slash command to act on it. This sidesteps all headless-wedge concerns.

**Tests:**
- `test_offer.py` — YAML parse valid/invalid, fingerprint stability across field reorder, fingerprint changes on wiki/scope/wiki_source change.
- `test_consent.py` — state classification across all six combinations in the state-machine table.
- `test_hooks_session_start.py` — TTY vs non-TTY behaviour, output contains the prompt line in the offered state.

**Acceptance:** starting Claude Code in a freshly-cloned `.lore.yml`-bearing repo produces the offer prompt; non-interactive invocations emit only to hook log.

---

### Phase 3 — `/lore:attach` rewrite + registry CLI

**`/lore:attach` behaviour matrix** (same command, different flags):

| Invocation | Behaviour |
|---|---|
| `/lore:attach` (no args) in a repo with `.lore.yml` | Accept offer: add attachment, ingest scope chain, check for root-wiki conflicts (prompt if any). |
| `/lore:attach --decline` | Add to `declined` with offer fingerprint. |
| `/lore:attach --manual --wiki W --scope S` | Manual attach with no `.lore.yml`. Creates/updates the attachment and ingests the scope chain. |
| `/lore:attach --offer --wiki W --scope S [--wiki-source URL]` | Write a `.lore.yml` at the repo root (for a maintainer turning this repo into a shareable offer). Does not auto-attach; re-run `/lore:attach` to accept. |
| `/lore:attach --migrate` | Run one-shot migration on the current repo (Phase 5 shares this code). |

**`/lore:detach` behaviour:** remove the attachment entry. Leaves the scope tree intact (other repos may still reference those scopes). If the detached attachment was the last reference to a scope subtree, emit a note ("scope `X` has no remaining attachments; keep in tree? `lore scope rm X` to remove").

**New CLI surface** (extending `lib/lore_cli/registry_cmd.py`):

- `lore attachments ls [--json]` — list all attachments.
- `lore attachments show <path>` — what is this path attached as (longest-prefix match; reports ambiguity if path isn't covered).
- `lore attachments rm <path>` — same as `/lore:detach` but by path.
- `lore scopes ls [--tree]` — list scopes; `--tree` renders using ID-prefix grouping.
- `lore scopes show <scope-id>` — label, wiki (resolved via inheritance), descendants, attachments under this scope.
- `lore scopes rename <old> <new>` — rename a scope; rewrites all descendant IDs + all attachment entries under the subtree. Confirmation required unless `--yes`.
- `lore scopes reparent <scope> <new-parent>` — move a subtree under a different parent. Same rewrite semantics.
- `lore scopes rm <scope-id>` — remove a leaf scope. Fails if attachments still reference it.

**Tests:**
- `test_attach_cmd.py` — rewrite to exercise the matrix above; drop CLAUDE.md fixtures, add `attachments.json` fixtures.
- `test_scopes_cmd.py` (new) — rename/reparent correctness, propagation to attachments, attachment-still-present safety.

**Acceptance:** the matrix above is fully covered; `lore scopes rename` propagates correctly; detach is reversible.

---

### Phase 4 — Doctor + orphan/unattached surfacing

**`lore doctor` additions** (extending the existing `lib/lore_cli/doctor_cmd.py` rather than `registry_cmd.py` — doctor is the umbrella):

- **Attachment checks:** each attachment's `path` exists; each `offer_fingerprint` matches the current `.lore.yml` (drift); each `wiki` corresponds to a real wiki dir; each `scope` is in `scopes.json`.
- **Scope-tree checks:** every scope's implicit parent (derived from ID) exists in `scopes.json`; every root scope has a `wiki` pointer; no orphaned scopes (in tree but with no attachment ancestor or descendant — soft warning, not an error).
- **Ledger cross-check:** the `__orphan__` and `__unattached__` buckets in `transcript-ledger` are surfaced as actionable items: *"N transcripts captured from path X — attach that path? (run `/lore:attach --manual ...` or remove with `lore attachments purge-unattached`)"*.

**New CLI:**
- `lore attachments purge-unattached` — for each unattached-bucket ledger entry, mark orphan+curator-stamped so pending() drops it forever. Equivalent to the current orphan handling but reachable by the user on demand.

**Tests:**
- `test_doctor_attachments.py` — each failure mode triggers its specific report.
- `test_doctor_scopes.py` — tree integrity checks.

**Acceptance:** `lore doctor` returns a well-classified list of issues; each issue has an actionable suggestion.

---

### Phase 5 — Migration tool

The migration tool is a **one-shot** `lore migrate attachments` command plus a **lazy fallback** triggered by the legacy resolver. Both share the same core function.

**Core function:** `lore_core/migration/attachments.py::migrate_repo(repo_path: Path, *, dry_run: bool) → MigrationResult`

Steps per repo:

1. **Discover the legacy block.** Look for `CLAUDE.md` at `repo_path` with a `## Lore` section. Parse via the existing `lore_core/attach.py::read_attach`.
2. **Write `.lore.yml`** at repo root with the same fields (wiki, scope, backend, issues, prs). Preserve any `wiki_source:`-equivalent if present (otherwise absent — user can add later).
3. **Append to `attachments.json`** with `source: "migrated"`, fingerprint of the new `.lore.yml`, `attached_at` = now.
4. **Ingest the scope chain** into `scopes.json` (backfill parents; inherit wiki from the offer).
5. **Strip the `## Lore` section** from `CLAUDE.md` — use the existing managed-block boundary (`## Lore` heading + `<!-- Managed by /lore:attach -->` comment). Leave surrounding content untouched.
6. **Leave a one-line breadcrumb** at the former block site:
   ```
   <!-- Lore attachment migrated to .lore.yml; state in $LORE_ROOT/.lore/ -->
   ```
   Purely informational; removable. This survives a second migration run (step 1 returns "no legacy block found" and the function exits early — idempotent).

**Idempotence invariants:**
- Running twice on the same repo: second run is a no-op (no legacy block → early exit, no re-write).
- Running on a repo with `.lore.yml` already present but no attachment: detects mismatch (`.lore.yml` present, no attachment matching fingerprint) and emits a warning, does not re-write `.lore.yml`.
- `dry_run=True` produces the full report without touching any file.

**Entry points:**

- **Explicit:** `lore migrate attachments [--root PATH] [--dry-run]`. Default root is `$HOME`; walks for `CLAUDE.md` files containing `## Lore`, excluding `.git/` and `node_modules/`. Reports a plan; requires `--yes` to execute.
- **Opt-in per-repo:** `lore migrate attachments --repo PATH`. Migrates a single repo.
- **Lazy (automatic):** when the legacy resolver is invoked (during Phase 1–4 transition), it auto-calls `migrate_repo(repo_path, dry_run=False)` once per discovered CLAUDE.md-with-block and emits a one-line hook event: `attachments-migrated-lazy`. User sees nothing. This is removed in Phase 6.

**Cleanup tool** (alongside migration): `lore migrate attachments --cleanup-breadcrumbs [--yes]` walks known attachments, strips the one-line breadcrumb comments from CLAUDE.md files. Optional; purely cosmetic.

**Tests:**
- `test_migration_attachments.py` — golden-file tests: a repo with `## Lore` migrates to the expected `.lore.yml` + state rows; CLAUDE.md is correctly trimmed; breadcrumb present; re-run is no-op; dry-run reports without writing.
- `test_migration_edge_cases.py` — repo with malformed `## Lore` block (missing fields), repo with two `## Lore` sections (pick first, warn), repo where `.lore.yml` already exists with different fields (refuse, report).

**Rollback:**

- The migration does not delete state files. To roll back: delete `.lore.yml`, remove the corresponding attachment from `attachments.json`, restore the `## Lore` section in CLAUDE.md from git history. No tooling provided for this — it's an emergency path, not a supported workflow.

**Acceptance:** a user with an existing wiki (ccat, private) runs `lore migrate attachments`, sees a dry-run report with the expected N repos, runs `--yes`, and the first session after is indistinguishable in behavior from before migration.

---

### Phase 6 — Retire walk-up + scar-tissue cleanup

Only after Phase 5 has shipped for long enough that telemetry confirms the lazy back-compat path is no longer firing (≥1 release cycle).

**Remove:**

- `_legacy_walk_up_resolve` in `scope_resolver.py`.
- The lazy migration hook.
- `lore_core/attach.py` — the CLAUDE.md parser. The `.lore.yml` parser replaces it.
- Dead test fixtures involving `## Lore` blocks.

**Simplify:**

- `curator_a.py:302` comment about "TOCTOU with re-parse CLAUDE.md" → delete the comment and the associated defensive code; the new resolver has no TOCTOU (single state file read per curator pass).
- `curator_a.py:424-427` historical note about the prior `lore_curator → lore_cli` layer violation → delete; no longer relevant.
- `TranscriptLedger._resolve_wiki_cached` and associated cache dict (`ledger.py:203-217`, `ledger.py:154`) → delete; `AttachmentsFile.longest_prefix_match` is already O(log n) with no cache needed.
- `lore_cli/registry_cmd.py` `show` subcommand's walk-up logic (lines 88–99) → replace with `AttachmentsFile.longest_prefix_match`.

**Tests:**
- Delete back-compat tests that exercised legacy `## Lore` block resolution.
- Verify curator pass count: `resolve_scope` is called exactly once per pending entry (was N+2 with two non-sharing caches; target is N or better with a single shared `AttachmentsFile` instance).

**Acceptance:** code search for `"## Lore"` in `lib/` returns zero hits outside of tests that specifically exercise legacy-migration behavior (which live under `tests/migration/`).

---

## Test strategy summary

- **New tests:** ~8 new test modules across the new state + consent + migration surfaces.
- **Rewritten tests:** ~6 existing tests migrate fixtures from CLAUDE.md to `attachments.json`. Behaviour assertions do not change.
- **Deleted tests:** legacy back-compat tests after Phase 6.
- **Test doubles:** `AttachmentsFile` and `ScopesFile` have filesystem-backed implementations; tests use `tmp_path`-backed instances — no mocking layer needed.
- **Golden files:** migration tests use golden `.lore.yml` and `attachments.json` snapshots for readability.
- **Property tests** (nice-to-have, not blocking): `longest_prefix_match` correctness via Hypothesis with random path sets.

## Rollout

- **Phase 1–2:** ship behind a `LORE_NEW_STATE=1` opt-in env var. Internal testing on personal wikis first.
- **Phase 3–4:** drop the env flag; new installs default to the new model. Existing installs auto-migrate lazily on first session per repo.
- **Phase 5:** ship `lore migrate attachments` as the sanctioned path; announce in changelog + README.
- **Phase 6:** release ≥1 cycle after Phase 5. Version bump in `.claude-plugin/plugin.json` (memory note: `project_lore_plugin_cache_stale`).

## Open questions from issue #22 to resolve during implementation

- **Wiki doesn't exist locally on accept.** Proposal: on accept, if `wiki_source:` is set, prompt `clone and attach?`; if no `wiki_source:`, prompt `create empty wiki or decline?`. Decide when writing Phase 3.
- **`$LORE_ROOT` rehoming.** Proposal: `lore attachments rehome --from PATH --to PATH` rewrites all attachment paths. Defer to a follow-up issue — not blocking for v1. (See cross-link with issue #6.)
- **Shared scope tree across teammates.** Out of scope for v1. The file split (`scopes.json` separable from `attachments.json`) makes this trivial to add later.

## Risks

- **Lazy migration races.** Two concurrent sessions in the same repo both see the legacy block and both try to migrate. Mitigation: migration acquires the existing curator lockfile for the write window; second session sees `.lore.yml` already present and takes the no-op path.
- **`.lore.yml` schema drift.** Future additions to the offer schema may invalidate existing fingerprints and re-prompt users unnecessarily. Mitigation: versioned fingerprint (`schema_version: 1` in `.lore.yml`, included in fingerprint only from the routing-relevant fields).
- **Silent wiki conflicts.** If two offers claim the same root with different wikis, the conflict prompt is the only safety net. Mitigation: doctor has a pre-check that detects latent conflicts even without a new accept in progress.

## Definition of done

- [ ] All six phases landed.
- [ ] Telemetry: zero firings of legacy resolver path for ≥1 release cycle.
- [ ] Concept note `[[local-lore-state]]` loses `draft: true` (frontmatter promotion).
- [ ] Issue #22 closed; follow-ups filed for rehoming and shared scope trees if still relevant.
- [ ] CHANGELOG entries for each phase.
