# Changelog

All notable changes are recorded here. The version in this file mirrors
`pyproject.toml` and `.claude-plugin/plugin.json` — bumping the package
version is what makes `claude plugin update lore@lore` re-fetch.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
(0.x means anything can change between minor versions until 1.0).

## [Unreleased]

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
