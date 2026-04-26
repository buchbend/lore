# Changelog

All notable changes are recorded here. The version in this file mirrors
`pyproject.toml` and `.claude-plugin/plugin.json` — bumping the package
version is what makes `claude plugin update lore@lore` re-fetch.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
(0.x means anything can change between minor versions until 1.0).

## [Unreleased]

## [0.10.0] — 2026-04-26

Cleanup-roadmap closeout (Phases 0-8). User-visible CLI/slash changes
warrant a minor bump; no breaking changes — every legacy form keeps
working via aliases.

### Added

- **`lore wiki new <name>`** — canonical home for wiki-lifecycle
  verbs going forward; matches `lore surface init/add/commit`. The
  legacy `lore new-wiki <name>` keeps working as an alias and prints
  a one-line stderr hint pointing at the new form.
- **Role-named curator modules**:
  - `lore_curator/session_curator.py` (was `curator_a.py`) — files
    session notes from completed transcripts.
  - `lore_curator/daily_curator.py` (was `curator_b.py`) — extracts
    surfaces and regenerates `threads.md`.
  - `lore_curator/defrag_curator.py` (was `curator_c.py`) — weekly
    defrag/stale-flag/supersession.
  Function aliases `run_session_curator` / `run_daily_curator` /
  `run_defrag_curator` are added alongside the legacy `run_curator_a/b/c`
  names; all old import sites continue to work.

### Changed

- **Slash command renamed**: `/lore:surface-new` → `/lore:surface-add`
  for symmetry with `lore surface add`. The skill directory was
  renamed via `git mv` (history preserved); autocomplete now shows
  the new name.
- **Skill cleanup**: `skills/lint/SKILL.md` and `skills/curator/SKILL.md`
  now call the `lore` CLI directly (`lore lint`, `lore curator`,
  `lore migrate`) instead of leaking internal package paths
  (`python -m lore_core.lint` etc.).
- **SKILL.md description sharpening**: `/lore:lint` and `/lore:curator`
  descriptions revised to make their distinct roles obvious to Claude
  (mechanical-vs-judgment) so picker reliability improves.

### Notes

- `tests/test_skill_cli_drift.py` is the static guard against future
  `python -m lore_*` regression in skills.
- `tests/test_cli_wiki.py` pins both the canonical `lore wiki new`
  path and the legacy `lore new-wiki` alias.
- Curator module renames are mechanical: ~25 import sites migrated;
  function aliases mean ~188 callers of `run_curator_a/b/c` keep
  working unchanged.

## [0.9.0] — 2026-04-25

Surface-extraction quality push (full notes in
`docs/superpowers/HANDOVER-2026-04-19.md` and the v0.9.0 commit). This
entry also closes a long version-sync drift: `.claude-plugin/plugin.json`
was stuck at 0.5.0 while `pyproject.toml` advanced to 0.9.0, meaning
`claude plugin update lore@lore` silently reused cached code. A pytest
guard (`tests/test_version_sync.py`) now fails CI if the two sources
disagree.

> **Note on the gap.** Versions 0.4.0 through 0.8.2 shipped without
> changelog entries; their notes live in commit messages
> (`git log --grep="Lore v0\."`). They will be backfilled in a future
> docs pass.

## [0.3.0] — 2026-04-22

Local-Lore-state release. Replaces the distributed `## Lore` CLAUDE.md
routing model with a host-local registry + optional `.lore.yml` offer.
CLAUDE.md is no longer a routing artifact. See issue #22 and
`docs/superpowers/plans/2026-04-22-local-lore-state-plan.md`.

### Added

- **Host-local state** — `$LORE_ROOT/.lore/attachments.json` (which
  paths route where) and `$LORE_ROOT/.lore/scopes.json` (the scope
  tree, flat ID-as-path with wiki-inheritance).
- **`.lore.yml` offer format** — optional checked-in repo file
  declaring `wiki`, `scope`, `backend`, `issues`, `prs`, and
  `wiki_source`. Fingerprinted over routing fields only so non-routing
  tweaks don't re-prompt users who accepted.
- **Consent state machine** — `UNTRACKED | OFFERED | ATTACHED |
  DORMANT | MANUAL | DRIFT` surfaced by a non-blocking notice in
  SessionStart when `.lore.yml` is pending acceptance.
- **Registry CLI** — `lore attach {accept, decline, manual, offer}`,
  `lore attachments {ls, show, rm, purge-unattached}`,
  `lore scopes {ls, show, rename, reparent, rm}`. Scope rename/reparent
  propagates across both state files atomically.
- **Doctor extensions** — `lore doctor` validates attachments (path
  exists, wiki dir exists, scope in tree, fingerprint matches) and
  scope-tree integrity; surfaces `__orphan__` / `__unattached__` ledger
  buckets with actionable suggestions.
- **Migration tool** — `lore migrate attachments` (one-shot,
  idempotent, dry-run) converts legacy `## Lore` CLAUDE.md blocks into
  `.lore.yml` + registry rows and strips the section from CLAUDE.md
  (surrounding content preserved).
- **Reinstall shortcut** — `lore install reinstall` composes
  `uninstall` + `install` in one step.

### Changed

- `resolve_scope(cwd)` is registry-only — longest-prefix match on
  `attachments.json`. No filesystem walk-up. O(log n) lookup.
- Ledger's `pending()` / `pending_by_wiki()` default resolver is
  bound to the ledger's own `lore_root`, not `$LORE_ROOT` env —
  simplifies test fixtures.
- `_walk_up_lore_config` is now a registry-backed shim that returns
  a synthetic `claude_md_path` sentinel plus a block dict derived from
  the resolved scope (merged with any `.lore.yml` at the attachment
  path for non-routing fields).

### Removed

- Legacy `## Lore` CLAUDE.md walk-up resolver (`_legacy_walk_up_resolve`).
- Lazy-migration hook in the legacy resolver (transition-only,
  superseded by explicit `lore migrate attachments`).
- `TranscriptLedger._resolve_wiki_cached` + cache dict (redundant now
  that longest-prefix match is O(log n)).
- Legacy `lore attach read` / `lore attach write` commands (replaced
  by `lore attach accept|manual|offer` + `lore attachments show`).

### Migration

On machines with existing `## Lore` blocks in CLAUDE.md:

```
lore migrate attachments --dry-run   # preview
lore migrate attachments --yes       # apply
```

Idempotent. Re-runs are no-op. Preserves surrounding CLAUDE.md content.

## [0.2.4] — 2026-04-21

### Fixed

- **Capture hooks were never registered with Claude Code after v0.2.3**.
  `.claude-plugin/plugin.json` gained `SessionEnd` + `lore hook capture`
  wiring for `SessionStart`/`PreCompact` in commit `004d033`, but the
  package version wasn't bumped. `claude plugin update lore@lore` had
  nothing to re-fetch, so installed plugin caches stayed on the
  pre-capture manifest — the banner hook fired but the capture hook
  never did, and no transcripts were ever ledgered unless curator was
  run by hand. Bump forces a re-fetch.
- `lore_search` FTS5 index auto-migrates from the legacy contentless
  schema (which couldn't DELETE). `/lore:resume <keyword>` and
  `lore_search` MCP calls no longer raise
  `cannot DELETE from contentless fts5 table: notes_fts`.

### Added

- `lore hook capture` now emits a `hook-events.jsonl` record with
  `outcome="no-scope"` when the cwd isn't inside a configured wiki
  instead of silently returning. Makes "hook never fired" vs "hook
  fired but declined" distinguishable in `lore status` and
  `lore runs list --hooks`.
- `lore status` gains a `Hook` line between `Last run` and `Pending`
  (`· Hook  12m ago · session-start · spawned-curator`) plus a
  loud-on-earning alert when pending > 0 AND no hook events in 24h.
- `lore runs list --hooks` prints a diagnostic banner when runs
  exist but `hook-events.jsonl` is empty/missing.

## [0.2.3] — 2026-04-18

### Fixed

- **Slash commands lost their `lore:` namespace prefix in v0.2.0**.
  When skill directories were renamed from `skills/lore:<name>/`
  to `skills/<name>/` and the SKILL.md frontmatter `name:` was
  set to the bare value, Claude Code's picker started showing
  bare slash commands (`/init`, `/resume`, `/inbox`, …) — colliding
  with built-ins like Claude Code's own `/init`.
- **Restored explicit scoping in SKILL.md frontmatter**:
  `name: lore:<bare>` (literal colon). Other plugins like
  `frontend-design:frontend-design` use the same pattern; Claude
  Code uses the frontmatter `name` field verbatim as the slash
  command name. Directory names stay bare; only the in-frontmatter
  name carries the prefix.

  After `claude plugin update lore@lore`, slash commands appear in
  the picker as `/lore:resume`, `/lore:loaded`, `/lore:init`, etc.
  No collision with built-ins; explicit namespace always visible.

## [0.2.2] — 2026-04-18

### Fixed

- **`claude plugin update lore@lore` failed** with "destination is
  empty after copy" because the v0.2.0 marketplace.json used a
  `github` source object pointing back at `buchbend/lore` — the
  same repo as the marketplace itself. Claude Code's update path
  for github-source plugins (clone source repo → copy into
  versioned cache) appears to mishandle this self-reference and
  produces an empty cache.
- **Switched to `source: "./"`** (validated cleanly with
  `claude plugin validate`). The marketplace root IS the plugin
  root in our setup; Claude Code uses the marketplace clone
  directly, no separate github source clone, no copy-step bug.

## [0.2.1] — 2026-04-18

### Fixed

- **Top-level `lore --help` was still argparse-style** (bare list of
  subcommand names). The 0.2.0 typer migration covered the leaves
  but `__main__.py` still used the legacy SUBCOMMANDS lookup. Now a
  proper `typer.Typer()` root mounts every subcommand via
  `add_typer`, so `lore --help` shows the Rich-boxed command tree
  with descriptions.
- **`lore mcp --help` hung waiting for stdin** because the MCP
  server's `main()` was bare argparse-free and ignored `--help`.
  Wrapped in a typer app with a no-arg callback so help works
  without starting the STDIO loop.
- **`lore migrate`** was still on argparse; migrated to typer.
- **`lore uninstall`** alias preserved as a top-level typer command
  forwarding to `install_cmd._cmd_install` with `mode="uninstall"`
  (same flags as `lore install uninstall`).

## [0.2.0] — 2026-04-18

### Added

- **`lore install` / `lore uninstall`** — multi-host installer
  dispatcher (Claude Code + Cursor in v1) with print-and-confirm UX,
  per-host plans, schema versioning per host module, semantic-undo
  contract, `--force --yes` refusal, legacy-artifact detection.
  Replaces the 340-line `install.sh`.
- **`lore doctor`** — smoke-test subcommand: LORE_ROOT, wikis, cache,
  MCP server, FTS backend, SessionStart hook, attach block.
- **`lore_core.resume.gather()`** — unified resume entry point covering
  no-arg / wiki / keyword / scope modes. `/lore:resume` skill now does
  one MCP call instead of ~6 iterative Glob/Read/Grep.
- **`lore_core.session.scaffold()`** + `lore session new` /
  `lore session commit` — split `/lore:session` into MCP scaffold-read
  + visible CLI write/commit. Subagent goes from 6–8 tool calls to ~3.
- **`lore_core.briefing.gather()`** + `lore briefing
  {gather,publish,mark}` — split `/lore:briefing` into deterministic
  MCP gather + LLM prose composition + visible CLI publish.
- **`lore_core.inbox.classify()`** + `lore inbox
  {classify,archive}` — same shape for `/lore:inbox`.
- **`lore resume <topic> --launch <host>`** — standalone launcher
  pre-warms a fresh agent session with a gathered context block.
  TOML host registry at `lib/lore_cli/hosts.d/*.toml` for cross-host
  dispatch (Claude + Cursor in v1).
- **3 new MCP tools**: `lore_session_scaffold`, `lore_briefing_gather`,
  `lore_inbox_classify` (now 9 total).
- **`/lore:loaded`** — renamed from `/lore:why`; matches the
  SessionStart status line text. Cache stores full text (truncation
  only on inject).
- **Vault-first directive** in SessionStart `additionalContext` and
  re-asserted in PreCompact (`templates/host-rules/default.md` is
  the single source of truth, used by hooks + Cursor rules file).
- **`tools/undo_install_sh.py`** — stdlib-only Python helper to
  cleanly reverse the legacy `install.sh` mutations.
- **CONTRIBUTING.md** — dev-mode install recipe, "filing a host
  module" guide, version-bump convention.

### Changed

- **CLI migrated from argparse to typer + Rich** across all 12
  subcommands. Pretty `--help` boxes, type-coerced options, future
  shell-completion. `lib/lore_cli/_compat.py:argv_main()` keeps the
  legacy `main(argv) -> int` contract for tests + the SUBCOMMANDS
  dispatcher.
- **Skill directory names dropped the `lore:` prefix** — Claude Code's
  plugin namespace supplies the prefix. `skills/lore:loaded/` →
  `skills/loaded/`. Slash commands stay `/lore:loaded` etc.
- **`--json` envelopes** standardised across `lore.<verb>/N` schemas
  for attach, detach, search, lint, curator, resume, session,
  briefing, inbox, doctor, install.
- **`install.sh`** shrunk from 340 lines to a ~50-line deprecation
  shim pointing users at `lore install`.
- **`lore-thesis.md`** reframed from "plugin with CLI underneath" to
  "CLI-first that ships plugins per host." Token-economy principle is
  now the architectural backbone (gather → CLI/MCP, synthesis at
  write/maintenance time, no synthesis at retrieval).
- **`.claude-plugin/marketplace.json`** schema fixed
  (`source: {"source": "github", "repo": "buchbend/lore"}`,
  `metadata.description` instead of root-level `description`).
- **`.claude-plugin/plugin.json`** declares `hooks` + `mcpServers`
  inline (Claude Code's plugin system wires them; install no longer
  mutates `~/.claude/settings.json`).

### Fixed

- **PyPI name `lore` is squatted** by an unrelated package
  (`lore 0.8.6`, broken on Python 3.13). Install path uses
  `pipx install git+https://github.com/buchbend/lore.git` until a
  clean PyPI name is picked (issue #9).
- **Marketplace registration step missing** in `lore install --host
  claude` — added `claude plugin marketplace add buchbend/lore`
  with `on_failure=continue` so re-runs don't wedge.
- **Install error messages printed literal `[red]...[/red]`** markup
  tags — `markup=False` (added for ANSI safety) suppressed the
  wrapper colour. Now uses `rich.markup.escape()` on user-derived
  content with `markup=True` on the wrapper.
- **`lore install --json` mixed Rich legacy warning with JSON
  envelope** — warning now suppressed when `--json` is set;
  artifacts ride in `legacy_artifacts` field.

### Filed as known gaps

- **#6** — `LORE_ROOT` portable resolver (`~/.config/lore/config.toml`
  fallback for host-agnostic resolution).
- **#7** — Verify `lore_mcp.server` protocol compatibility with
  Cursor's MCP client.
- **#8** — Windows support for `lore install` and the Cursor host
  adapter.
- **#9** — Pick a clean PyPI name (`lore` is squatted).

## [0.1.0] — 2026-04-17 (initial alpha)

Initial public release. Linter, schema v2 with `## Issues touched` /
`## Loose ends` sections, MCP server, FTS5 search, curator (stale
detection + supersession + git-date backfill), session-writer
subagent, briefing sinks (Matrix, markdown), `lore attach` /
`lore detach`, scope-prefix `lore resume --scope`, identity +
team-mode machinery.
