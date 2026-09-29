# Code sweep: callers and jobs for `lib/`

Swept at `c1e4081` on branch `epic/419` on 2026-09-29, for issue 423. The
issue belongs to PRD (product requirements document) 0014,
`docs/prd/0014-retire-flags-attach-facts-to-artifacts.md`. The sweep ran after
the branch retired flags, briefings and the LLM (large language model) client.
The report changes no code. The owner decides every deletion.

`lib/` holds 139 Python modules and 31,503 lines at this commit. `skills/` and
`lore-workflow/` define no module, verb or tool. Their skill files appear
below as callers.

## Method

- **Modules.** An AST (abstract syntax tree) walk collects every `import` and
  `from … import` in `lib/` and `tools/`. The walk includes imports inside
  functions and relative imports. An import of `a.b.c` also counts for the
  packages `a` and `a.b`.
- **Prose callers.** A module with no importer is searched for in skill files,
  templates and manifests. Examples are `pyproject.toml`'s entry point and a
  `python -m` string passed to a subprocess.
- **Verbs.** The Typer app behind the `lore` CLI (command-line interface)
  gives every verb. A verb's caller is a manifest, a skill file, a template, a
  subprocess call, a docs page, or a user-facing message in another module. The ranking follows that order. The verb's own
  module does not count.
- **Tools.** The same search runs for each MCP (Model Context Protocol) tool
  name. `lib/lore_mcp/server.py` defines the tools and does not count.
- **Config keys.** The dataclass trees behind `lore config` give the root and
  wiki keys. `offer.py` gives the `.lore.yml` keys. A grep for `os.environ`
  gives the `LORE_*` variables. A key's caller is the line that reads its
  value. A docs page that only lists a key is not a reader.
- **Not callers.** Tests, `CHANGELOG.md`, `CONTEXT.md`, `CONTEXT-FORMAT.md`,
  PRDs, ADRs (architecture decision records), `brainstorms/`, `.lore/` and the
  earlier sweep (`docs/session-note-teardown-sweep.md`). These files record history or
  define terms. They do not call anything.
- **Reach.** A walk from the `lore` entry point over the import graph
  finds three modules nothing reaches: `publish_gate.py`, `redaction.py` and
  `secrets_env.py`.
- **Check.** A throwaway script opened every `path:line` below. It confirmed
  that the file exists and that the line names the row.

Limits of the method:

- The import graph works per module, not per symbol. A module that only a
  dead function imports still counts as live. The earlier sweep did the
  symbol-level pass.
- A verb with no caller can still be typed by a person. "No caller" means no
  manifest, skill, template, docs page or code path names the verb.

## How to read the tables

- **Last caller** — one caller that keeps the row alive, as `path:line`.
  `(+N)` counts the other files that call it. "none" means the search found
  no caller.
- **Kind** — how the caller reaches the row:
  - `import`, `subprocess`, `manifest`, `skill`, `template` or `docs`.
  - `code message` — a hint string in another module.
  - `read` — a line that loads the key or file.
  - `config` — a line in a Vale config.
- **Job** — one of the four jobs PRD 0014 keeps:
  - `1 artifact map` — the artifact map and team language: writing rules,
    glossary, Vale, and where ADRs, PRDs, docs and issues live.
  - `2 wiki notes` — wiki topic notes at session start.
  - `3 retrieval` — look-first retrieval: tier resolve, context pack, code
    map, federated search.
  - `4 capture` — transcript capture.
  - `all four` — plumbing that every job loads, such as config, install,
    hooks and the event log.
  - `none` — the row serves no retained job.

The owner ruled language control core, so `lore style` and the Vale files sit
under job 1. PRD 0015 plans to merge the workflow skills. This sweep puts the
`lore_workflow` helpers under job 1, because they read and write ADRs, PRDs,
epics and roadmaps.

## Summary

| Kind | Rows | 1 artifact map | 2 wiki notes | 3 retrieval | 4 capture | all four | none | No caller |
| --- | --: | --: | --: | --: | --: | --: | --: | --: |
| Modules | 139 | 13 | 37 | 25 | 19 | 36 | 9 | 2 |
| CLI verbs | 79 | 7 | 29 | 7 | 5 | 20 | 11 | 24 |
| MCP tools | 12 | 2 | 1 | 8 | 0 | 0 | 1 | 0 |
| Config keys | 40 | 0 | 11 | 3 | 3 | 14 | 9 | 11 |

| Job | Module lines |
| --- | --: |
| 1 artifact map | 1,928 |
| 2 wiki notes | 9,531 |
| 3 retrieval | 5,269 |
| 4 capture | 3,035 |
| all four | 9,812 |
| none | 1,928 |

The deletion candidates below hold 17 rows.

## Modules

One row per `.py` file under `lib/`. Paths drop the `lib/` prefix.

| Module | Lines | Last caller | Job | Note |
| --- | --: | --- | --- | --- |
| `lore_adapters/__init__.py` | 19 | `lib/lore_cli/hooks.py:310` (+3) · import | 4 capture |  |
| `lore_adapters/claude_code.py` | 284 | `lib/lore_adapters/registry.py:42` · import | 4 capture | Claude Code transcript reader |
| `lore_adapters/manual_send.py` | 162 | `lib/lore_adapters/registry.py:43` · import | 4 capture | backs `lore ingest` |
| `lore_adapters/protocol.py` | 83 | `lib/lore_adapters/__init__.py:5` (+1) · import | 4 capture |  |
| `lore_adapters/registry.py` | 46 | `lib/lore_adapters/__init__.py:6` · import | 4 capture |  |
| `lore_cli/__init__.py` | 6 | `lib/lore_cli/__main__.py:58` (+30) · import | all four | plumbing |
| `lore_cli/__main__.py` | 239 | `pyproject.toml:46` · manifest | all four | the `lore` entry point |
| `lore_cli/_argv_compat.py` | 74 | `lib/lore_cli/attach_cmd.py:31` (+26) · import | all four | plumbing |
| `lore_cli/_cli_helpers.py` | 45 | `lib/lore_cli/attach_cmd.py:101` (+5) · import | all four | plumbing |
| `lore_cli/_crash_log.py` | 165 | `lib/lore_cli/__main__.py:213` (+3) · import | all four | hook crash backstop |
| `lore_cli/_janitor_entry.py` | 35 | `lib/lore_cli/hooks.py:582` · import | all four | log retention |
| `lore_cli/_proc_wrapper.py` | 52 | `lib/lore_cli/spawn.py:240` · subprocess | 4 capture | launched as `python -m` by the sync spawn |
| `lore_cli/attach_cmd.py` | 985 | `lib/lore_cli/__main__.py:58` (+1) · import | 2 wiki notes | routes a repo to a wiki and scope |
| `lore_cli/attachments_cmd.py` | 199 | `lib/lore_cli/attach_cmd.py:30` · import | 2 wiki notes |  |
| `lore_cli/codemap_cmd.py` | 53 | `lib/lore_cli/__main__.py:58` · import | 3 retrieval |  |
| `lore_cli/config_cmd.py` | 359 | `lib/lore_cli/__main__.py:58` · import | all four | plumbing |
| `lore_cli/context_cache.py` | 167 | `lib/lore_cli/hooks.py:52` · import | 2 wiki notes | backs `/lore:context` |
| `lore_cli/curator_cmd.py` | 53 | `lib/lore_cli/__main__.py:58` · import | 2 wiki notes | wiki hygiene |
| `lore_cli/doctor_cmd.py` | 932 | `lib/lore_cli/__main__.py:58` (+2) · import | all four | install check |
| `lore_cli/drill_cmd.py` | 130 | `lib/lore_cli/__main__.py:58` · import | 3 retrieval |  |
| `lore_cli/hooks.py` | 1011 | `lib/lore_cli/__main__.py:58` · import | all four | every plugin hook |
| `lore_cli/inbox_cmd.py` | 70 | `lib/lore_cli/__main__.py:58` · import | 2 wiki notes | files inbox items as wiki notes |
| `lore_cli/ingest_cmd.py` | 90 | `lib/lore_cli/__main__.py:58` · import | 4 capture |  |
| `lore_cli/init_cmd.py` | 517 | `lib/lore_cli/__main__.py:58` · import | all four | onboarding |
| `lore_cli/install_cmd.py` | 923 | `lib/lore_cli/__main__.py:58` (+1) · import | all four | install and update |
| `lore_cli/journal_cmd.py` | 138 | `lib/lore_cli/__main__.py:58` · import | none | parked feature, hidden verb |
| `lore_cli/lint_cmd.py` | 42 | `lib/lore_cli/__main__.py:58` · import | 2 wiki notes | regenerates wiki catalogs |
| `lore_cli/mcp_cmd.py` | 37 | `lib/lore_cli/__main__.py:58` · import | 3 retrieval |  |
| `lore_cli/migrate_cmd.py` | 107 | `lib/lore_cli/__main__.py:58` · import | 2 wiki notes | frontmatter upgrade; `flag-blocks` is flag residue |
| `lore_cli/project_cmd.py` | 223 | `lib/lore_cli/__main__.py:58` · import | 2 wiki notes | project orientation notes |
| `lore_cli/quarantine_cmd.py` | 158 | `lib/lore_cli/__main__.py:58` · import | none | reads what the dead publish gate wrote |
| `lore_cli/scopes_cmd.py` | 486 | `lib/lore_cli/__main__.py:58` · import | 2 wiki notes |  |
| `lore_cli/search_cmd.py` | 116 | `lib/lore_cli/__main__.py:58` · import | 3 retrieval |  |
| `lore_cli/session_cmd.py` | 97 | `lib/lore_cli/__main__.py:58` · import | 2 wiki notes | commits a note inside a wiki |
| `lore_cli/spawn.py` | 287 | `lib/lore_cli/hooks.py:320` · import | 4 capture | detached transcript sync |
| `lore_cli/status_cmd.py` | 495 | `lib/lore_cli/__main__.py:58` · import | all four | health dashboard |
| `lore_cli/style_cmd.py` | 114 | `lib/lore_cli/__main__.py:58` · import | 1 artifact map | writing rules and Vale |
| `lore_cli/tier_cmd.py` | 46 | `lib/lore_cli/__main__.py:58` · import | 3 retrieval |  |
| `lore_cli/toggle_cmd.py` | 89 | `lib/lore_cli/__main__.py:58` · import | all four | `lore on` and `lore off` |
| `lore_cli/trace_cmd.py` | 150 | `lib/lore_cli/__main__.py:58` · import | all four | observability |
| `lore_cli/transcripts_cmd.py` | 166 | `lib/lore_cli/__main__.py:58` · import | 4 capture |  |
| `lore_cli/wiki_cmd.py` | 141 | `lib/lore_cli/__main__.py:58` (+1) · import | 2 wiki notes |  |
| `lore_cli/workflow_cmd.py` | 174 | `lib/lore_cli/__main__.py:58` · import | 1 artifact map | epic, PRD and roadmap helpers |
| `lore_core/__init__.py` | 22 | `lib/lore_adapters/claude_code.py:36` (+86) · import | all four | plumbing |
| `lore_core/attach.py` | 71 | `lib/lore_cli/attach_cmd.py:36` · import | 2 wiki notes | only strips a legacy `## Lore` block |
| `lore_core/breadcrumb.py` | 194 | `lib/lore_cli/hooks.py:995` (+1) · import | 2 wiki notes | session-start banner |
| `lore_core/capture_state.py` | 185 | `lib/lore_cli/status_cmd.py:46` (+1) · import | 4 capture |  |
| `lore_core/codemap/__init__.py` | 656 | `lib/lore_cli/codemap_cmd.py:15` (+4) · import | 3 retrieval |  |
| `lore_core/codemap/languages.py` | 179 | `lib/lore_core/codemap/__init__.py:400` · import | 3 retrieval |  |
| `lore_core/codemap/query.py` | 122 | `lib/lore_mcp/server.py:1051` · import | 3 retrieval |  |
| `lore_core/config.py` | 219 | `lib/lore_cli/_cli_helpers.py:19` (+24) · import | all four | resolves `LORE_ROOT` |
| `lore_core/config_schema.py` | 349 | `lib/lore_cli/config_cmd.py:11` (+3) · import | all four | plumbing |
| `lore_core/consent.py` | 135 | `lib/lore_cli/attach_cmd.py:213` (+1) · import | 2 wiki notes |  |
| `lore_core/context_pack.py` | 105 | `lib/lore_mcp/server.py:1032` · import | 3 retrieval |  |
| `lore_core/disagreement.py` | 96 | `lib/lore_mcp/server.py:163` (+1) · import | 3 retrieval | freshness |
| `lore_core/drain.py` | 237 | `lib/lore_cli/status_cmd.py:232` (+3) · import | 4 capture | sync news for `lore status` |
| `lore_core/errors.py` | 50 | `lib/lore_mcp/server.py:39` (+2) · import | 3 retrieval | MCP error envelope |
| `lore_core/freshness.py` | 504 | `lib/lore_mcp/server.py:51` (+1) · import | 3 retrieval | also feeds the session-start chip |
| `lore_core/freshness_filter.py` | 265 | `lib/lore_mcp/server.py:58` · import | 3 retrieval |  |
| `lore_core/gh.py` | 76 | `lib/lore_mcp/server.py:332` (+1) · import | 3 retrieval | federated GitHub search |
| `lore_core/git.py` | 190 | `lib/lore_cli/attach_cmd.py:471` (+7) · import | all four | plumbing |
| `lore_core/git_sync.py` | 649 | `lib/lore_cli/attach_cmd.py:182` (+3) · import | 2 wiki notes | wiki auto-pull at session start |
| `lore_core/identity.py` | 136 | `lib/lore_curator/hygiene.py:31` (+2) · import | all four | plumbing |
| `lore_core/inbox.py` | 159 | `lib/lore_cli/inbox_cmd.py:24` (+1) · import | 2 wiki notes |  |
| `lore_core/install/__init__.py` | 36 | `lib/lore_cli/init_cmd.py:310` (+5) · import | all four | install |
| `lore_core/install/_helpers.py` | 698 | `lib/lore_cli/install_cmd.py:44` (+3) · import | all four | install |
| `lore_core/install/base.py` | 135 | `lib/lore_cli/install_cmd.py:49` (+3) · import | all four | install |
| `lore_core/install/claude.py` | 196 | `lib/lore_core/install/__init__.py:23` · import | all four | install |
| `lore_core/install/cursor.py` | 525 | `lib/lore_core/install/__init__.py:23` · import | all four | install |
| `lore_core/io.py` | 136 | `lib/lore_cli/hooks.py:32` (+15) · import | all four | plumbing |
| `lore_core/janitor.py` | 349 | `lib/lore_cli/_janitor_entry.py:23` (+1) · import | all four | log retention |
| `lore_core/journal.py` | 263 | `lib/lore_cli/hooks.py:408` (+2) · import | none | parked feature |
| `lore_core/ledger.py` | 272 | `lib/lore_cli/attachments_cmd.py:157` (+5) · import | 4 capture |  |
| `lore_core/linkage.py` | 108 | `lib/lore_curator/ledger_linkage.py:39` (+2) · import | 4 capture |  |
| `lore_core/lint.py` | 1062 | `lib/lore_cli/lint_cmd.py:8` (+3) · import | 2 wiki notes |  |
| `lore_core/lockfile.py` | 74 | `lib/lore_cli/spawn.py:192` (+4) · import | all four | plumbing |
| `lore_core/managed_files.py` | 253 | `lib/lore_core/install/_helpers.py:32` (+2) · import | all four | install |
| `lore_core/migrate.py` | 357 | `lib/lore_cli/migrate_cmd.py:19` · import | 2 wiki notes |  |
| `lore_core/note_document.py` | 215 | `lib/lore_workflow/seed_epic.py:19` (+2) · import | none | reads session notes nothing writes |
| `lore_core/offer.py` | 188 | `lib/lore_cli/attach_cmd.py:121` (+6) · import | 2 wiki notes | `.lore.yml` |
| `lore_core/projects/__init__.py` | 17 | `lib/lore_cli/attach_cmd.py:472` (+3) · import | 2 wiki notes |  |
| `lore_core/projects/agent_sync.py` | 215 | `lib/lore_cli/project_cmd.py:29` (+1) · import | 2 wiki notes |  |
| `lore_core/projects/harness_parser.py` | 390 | `lib/lore_core/projects/stub_generator.py:33` · import | 2 wiki notes | relative import |
| `lore_core/projects/stub_generator.py` | 472 | `lib/lore_cli/attach_cmd.py:472` · import | 2 wiki notes |  |
| `lore_core/publish_gate.py` | 397 | none | none | gate over session-note chapters |
| `lore_core/quarantine.py` | 207 | `lib/lore_cli/quarantine_cmd.py:23` (+1) · import | none | fed only by the publish gate |
| `lore_core/redaction.py` | 159 | `lib/lore_core/publish_gate.py:36` · import | none | imported only by the publish gate |
| `lore_core/ref_verify.py` | 207 | `lib/lore_core/note_document.py:29` · import | none | only constants reach `note_document` |
| `lore_core/repo_docs.py` | 106 | `lib/lore_mcp/server.py:976` (+1) · import | 1 artifact map | where ADRs and PRDs live |
| `lore_core/root_config.py` | 234 | `lib/lore_cli/_janitor_entry.py:24` (+7) · import | all four | plumbing |
| `lore_core/run_log.py` | 237 | `lib/lore_curator/hygiene.py:34` · import | 2 wiki notes | curator run records |
| `lore_core/run_reader.py` | 211 | `lib/lore_core/capture_state.py:73` (+1) · import | all four | observability |
| `lore_core/run_retention.py` | 125 | `lib/lore_core/janitor.py:57` · import | all four | log retention |
| `lore_core/schema.py` | 206 | `lib/lore_cli/migrate_cmd.py:25` (+15) · import | 2 wiki notes | wiki note frontmatter |
| `lore_core/scope_resolver.py` | 56 | `lib/lore_cli/attachments_cmd.py:158` (+8) · import | 2 wiki notes |  |
| `lore_core/scopes.py` | 182 | `lib/lore_cli/hooks.py:33` (+1) · import | 2 wiki notes |  |
| `lore_core/secrets_env.py` | 184 | none | none | secrets for the retired LLM client |
| `lore_core/session.py` | 110 | `lib/lore_cli/session_cmd.py:17` (+2) · import | 2 wiki notes | attach lookup and note commit |
| `lore_core/session_start.py` | 664 | `lib/lore_cli/hooks.py:38` · import | 2 wiki notes |  |
| `lore_core/source_root.py` | 132 | `lib/lore_core/install/_helpers.py:45` (+3) · import | all four | install |
| `lore_core/spawn_gate.py` | 54 | `lib/lore_cli/hooks.py:312` · import | 3 retrieval | tier gate on subagent spawns |
| `lore_core/spine.py` | 403 | `lib/lore_cli/_crash_log.py:129` (+12) · import | all four | event log |
| `lore_core/stale_marker_writer.py` | 186 | `lib/lore_mcp/server.py:754` · import | 3 retrieval | freshness |
| `lore_core/state/__init__.py` | 17 | `lib/lore_cli/attach_cmd.py:153` (+12) · import | 2 wiki notes |  |
| `lore_core/state/attachments.py` | 256 | `lib/lore_cli/attach_cmd.py:215` (+13) · import | 2 wiki notes |  |
| `lore_core/state/scope_renames.py` | 91 | `lib/lore_cli/scopes_cmd.py:239` · import | 2 wiki notes |  |
| `lore_core/state/scopes.py` | 235 | `lib/lore_cli/attach_cmd.py:216` (+3) · import | 2 wiki notes |  |
| `lore_core/state/workflow_scaffold.py` | 75 | `lib/lore_cli/attach_cmd.py:153` · import | 1 artifact map |  |
| `lore_core/style.py` | 223 | `lib/lore_cli/style_cmd.py:24` · import | 1 artifact map | writing rules and Vale |
| `lore_core/templates/__init__.py` | 34 | `lib/lore_cli/init_cmd.py:46` (+2) · import | 2 wiki notes | wiki and root CLAUDE.md templates |
| `lore_core/tiers/__init__.py` | 72 | `lib/lore_cli/tier_cmd.py:14` (+1) · import | 3 retrieval |  |
| `lore_core/tiers/table.py` | 27 | `lib/lore_core/tiers/__init__.py:21` · import | 3 retrieval |  |
| `lore_core/timefmt.py` | 137 | `lib/lore_cli/config_cmd.py:12` (+5) · import | all four | plumbing |
| `lore_core/toggles.py` | 69 | `lib/lore_cli/hooks.py:392` (+2) · import | all four | mute state |
| `lore_core/tool_categories.py` | 86 | `lib/lore_adapters/claude_code.py:36` (+1) · import | 4 capture |  |
| `lore_core/trace.py` | 143 | `lib/lore_cli/trace_cmd.py:15` · import | all four | observability |
| `lore_core/transcript_sync.py` | 319 | `lib/lore_cli/transcripts_cmd.py:27` · import | 4 capture |  |
| `lore_core/types.py` | 120 | `lib/lore_adapters/claude_code.py:37` (+9) · import | 4 capture |  |
| `lore_core/verdicts_sidecar.py` | 113 | `lib/lore_mcp/server.py:142` (+1) · import | 3 retrieval | freshness |
| `lore_core/wiki_config.py` | 180 | `lib/lore_cli/config_cmd.py:140` (+3) · import | 2 wiki notes | `.lore-wiki.yml` |
| `lore_core/wikilinks.py` | 223 | `lib/lore_core/lint.py:906` (+2) · import | 2 wiki notes |  |
| `lore_curator/__init__.py` | 15 | `lib/lore_cli/curator_cmd.py:44` (+2) · import | 4 capture |  |
| `lore_curator/capture_routing.py` | 191 | `lib/lore_cli/hooks.py:47` · import | 4 capture |  |
| `lore_curator/hygiene.py` | 561 | `lib/lore_cli/curator_cmd.py:44` · import | 2 wiki notes | wiki frontmatter upkeep |
| `lore_curator/ledger_linkage.py` | 313 | `lib/lore_curator/capture_routing.py:105` · import | 4 capture |  |
| `lore_mcp/__init__.py` | 5 | `lib/lore_cli/drill_cmd.py:27` (+3) · import | 3 retrieval |  |
| `lore_mcp/reindex_watcher.py` | 140 | `lib/lore_mcp/server.py:193` · import | 3 retrieval |  |
| `lore_mcp/server.py` | 1566 | `lib/lore_cli/drill_cmd.py:27` (+2) · import | 3 retrieval |  |
| `lore_search/__init__.py` | 12 | `lib/lore_cli/search_cmd.py:17` (+4) · import | 3 retrieval |  |
| `lore_search/fts.py` | 550 | `lib/lore_cli/search_cmd.py:17` (+4) · import | 3 retrieval |  |
| `lore_search/query_log.py` | 109 | `lib/lore_mcp/server.py:232` (+1) · import | 3 retrieval |  |
| `lore_workflow/__init__.py` | 10 | `lib/lore_cli/attach_cmd.py:154` (+3) · import | 1 artifact map |  |
| `lore_workflow/board_parser.py` | 138 | `lib/lore_cli/workflow_cmd.py:15` · import | 1 artifact map | epic supervision board |
| `lore_workflow/diataxis.py` | 155 | `lore-workflow/skills/consolidate-docs/SKILL.md:26` (+3) · skill | 1 artifact map | docs quadrant for doc skills |
| `lore_workflow/epic_policy.py` | 87 | `lib/lore_cli/workflow_cmd.py:16` · import | 1 artifact map |  |
| `lore_workflow/prd_docs.py` | 181 | `lib/lore_cli/workflow_cmd.py:17` (+1) · import | 1 artifact map | PRD homes |
| `lore_workflow/roadmap_validator.py` | 442 | `lib/lore_cli/workflow_cmd.py:18` (+1) · import | 1 artifact map |  |
| `lore_workflow/scaffold.py` | 135 | `lib/lore_cli/attach_cmd.py:154` · import | 1 artifact map | ADR and PRD index stubs |
| `lore_workflow/seed_epic.py` | 88 | `lib/lore_cli/workflow_cmd.py:19` · import | 1 artifact map | reads session notes nothing writes |

### Package data

`pyproject.toml` ships these non-Python files under `lib/`.

| File | Last caller | Job | Note |
| --- | --- | --- | --- |
| `lore_core/styles/writing-rules.md` | `lib/lore_core/style.py:50` · read | 1 artifact map | `lore style show writing-rules` |
| `lore_core/styles/vale/vale.ini` | `lib/lore_core/style.py:79` · read | 1 artifact map | `lore style vale-config` |
| `lore_core/styles/vale/WritingRules/*.yml` (6 rules) | `lib/lore_core/styles/vale/vale.ini:9` · config | 1 artifact map | `BasedOnStyles = WritingRules` |
| `lore_core/templates/root-CLAUDE.md` | `lib/lore_cli/init_cmd.py:138` · read | 2 wiki notes | |
| `lore_core/templates/wiki-CLAUDE.md` | `lib/lore_cli/wiki_cmd.py:86` · read | 2 wiki notes | |
| `lore_core/templates/session.md` | `lib/lore_cli/wiki_cmd.py:88` · read | none | session-note template; see "Unclear" |
| `lore_core/templates/integration-rules/default.md` | `lib/lore_core/session_start.py:94` · read | 3 retrieval | the look-first directive |

## CLI verbs

One row per leaf verb, plus the bare forms of `lore install`, `lore attach` and
`lore config`, which run without a subcommand.

| Verb | Defined in | Last caller | Job | Note |
| --- | --- | --- | --- | --- |
| `lore uninstall` | `lore_cli/install_cmd.py` | `README.md:132` (+2) · docs | all four |  |
| `lore update` | `lore_cli/install_cmd.py` | `docs/how-to/onboarding.md:31` · docs | all four |  |
| `lore install` | `lore_cli/install_cmd.py` | `CONTRIBUTING.md:24` (+9) · docs | all four | bare form |
| `lore install check` | `lore_cli/install_cmd.py` | `CONTRIBUTING.md:48` · docs | all four |  |
| `lore install upgrade` | `lore_cli/install_cmd.py` | none | all four |  |
| `lore install uninstall` | `lore_cli/install_cmd.py` | `docs/architecture/cli-contract.md:90` (+1) · docs | all four |  |
| `lore install reinstall` | `lore_cli/install_cmd.py` | none | all four |  |
| `lore init` | `lore_cli/init_cmd.py` | `README.md:94` (+11) · docs | all four |  |
| `lore attach` | `lore_cli/attach_cmd.py` | `lore-workflow/skills/ccat-workflow-init/SKILL.md:3` (+9) · skill | 2 wiki notes | bare interactive form |
| `lore attach accept` | `lore_cli/attach_cmd.py` | `docs/architecture/state.md:36` (+3) · docs | 2 wiki notes |  |
| `lore attach decline` | `lore_cli/attach_cmd.py` | `docs/architecture/state.md:108` · docs | 2 wiki notes |  |
| `lore attach manual` | `lore_cli/attach_cmd.py` | `docs/architecture/state.md:107` · docs | 2 wiki notes |  |
| `lore attach offer` | `lore_cli/attach_cmd.py` | none | 2 wiki notes |  |
| `lore attach remove` | `lore_cli/attach_cmd.py` | `docs/how-to/troubleshooting.md:96` · docs | 2 wiki notes |  |
| `lore attach attachments ls` | `lore_cli/attachments_cmd.py` | none | 2 wiki notes |  |
| `lore attach attachments show` | `lore_cli/attachments_cmd.py` | `README.md:275` · docs | 2 wiki notes |  |
| `lore attach attachments rm` | `lore_cli/attachments_cmd.py` | none | 2 wiki notes |  |
| `lore attach attachments purge-unattached` | `lore_cli/attachments_cmd.py` | none | 2 wiki notes |  |
| `lore status` | `lore_cli/status_cmd.py` | `README.md:310` (+10) · docs | all four |  |
| `lore doctor` | `lore_cli/doctor_cmd.py` | `lore-workflow/skills/file-issue/SKILL.md:92` (+13) · skill | all four |  |
| `lore config` | `lore_cli/config_cmd.py` | `docs/architecture/config.md:32` · docs | all four | bare form |
| `lore config show` | `lore_cli/config_cmd.py` | `docs/architecture/config.md:39` · docs | all four |  |
| `lore config get` | `lore_cli/config_cmd.py` | `lore-workflow/skills/implement-issue/SKILL.md:108` (+3) · skill | all four |  |
| `lore config set` | `lore_cli/config_cmd.py` | `docs/architecture/config.md:42` · docs | all four |  |
| `lore config unset` | `lore_cli/config_cmd.py` | `docs/architecture/config.md:43` · docs | all four |  |
| `lore config edit` | `lore_cli/config_cmd.py` | `docs/architecture/config.md:45` · docs | all four |  |
| `lore config schema` | `lore_cli/config_cmd.py` | `docs/architecture/config.md:47` · docs | all four |  |
| `lore search` | `lore_cli/search_cmd.py` | none | 3 retrieval | agents use the `lore_search` tool |
| `lore drill` | `lore_cli/drill_cmd.py` | `docs/architecture/lore-drill.md:1` · docs | 3 retrieval |  |
| `lore session commit` | `lore_cli/session_cmd.py` | `skills/inbox/SKILL.md:38` · skill | 2 wiki notes |  |
| `lore project status` | `lore_cli/project_cmd.py` | none | 2 wiki notes |  |
| `lore project sync` | `lore_cli/project_cmd.py` | `lib/lore_core/lint.py:501` · code message | 2 wiki notes |  |
| `lore wiki new` | `lore_cli/wiki_cmd.py` | `lib/lore_cli/doctor_cmd.py:94` · code message | 2 wiki notes |  |
| `lore lint` | `lore_cli/lint_cmd.py` | `skills/curator/SKILL.md:6` (+5) · skill | 2 wiki notes |  |
| `lore curator` | `lore_cli/curator_cmd.py` | `skills/curator/SKILL.md:43` (+5) · skill | 2 wiki notes |  |
| `lore on` | `lore_cli/toggle_cmd.py` | `docs/architecture/slash-toggles.md:98` (+1) · docs | all four |  |
| `lore off` | `lore_cli/toggle_cmd.py` | `skills/context/SKILL.md:32` (+1) · skill | all four |  |
| `lore tier resolve` | `lore_cli/tier_cmd.py` | `lore-workflow/skills/orchestrate-epic/SKILL.md:103` (+10) · skill | 3 retrieval |  |
| `lore style show` | `lore_cli/style_cmd.py` | `lore-workflow/skills/domain-modeling/CONTEXT-FORMAT.md:28` (+7) · skill | 1 artifact map |  |
| `lore style vale-config` | `lore_cli/style_cmd.py` | `lore-workflow/skills/file-issue/SKILL.md:98` (+2) · skill | 1 artifact map |  |
| `lore codemap` | `lore_cli/codemap_cmd.py` | `lore-workflow/skills/brief/SKILL.md:26` (+7) · skill | 3 retrieval |  |
| `lore hook session-start` | `lore_cli/hooks.py` | `.claude-plugin/plugin.json:22` (+4) · manifest | 2 wiki notes |  |
| `lore hook pre-compact` | `lore_cli/hooks.py` | `.claude-plugin/plugin.json:40` (+1) · manifest | 3 retrieval | re-asserts the look-first directive |
| `lore hook stop` | `lore_cli/hooks.py` | `.claude-plugin/plugin.json:78` (+1) · manifest | none | body is a no-op |
| `lore hook spawn-model-gate` | `lore_cli/hooks.py` | `.claude-plugin/plugin.json:89` · manifest | 3 retrieval | tier gate |
| `lore hook context-log` | `lore_cli/hooks.py` | `skills/context/SKILL.md:12` · skill | 2 wiki notes |  |
| `lore hook user-prompt-submit` | `lore_cli/hooks.py` | `.claude-plugin/plugin.json:58` · manifest | 4 capture | mid-session registration |
| `lore hook capture` | `lore_cli/hooks.py` | `.claude-plugin/plugin.json:30` · manifest | 4 capture |  |
| `lore inbox classify` | `lore_cli/inbox_cmd.py` | none | 2 wiki notes | skill uses the MCP tool |
| `lore inbox archive` | `lore_cli/inbox_cmd.py` | `skills/inbox/SKILL.md:5` (+1) · skill | 2 wiki notes |  |
| `lore journal write` | `lore_cli/journal_cmd.py` | none | none | hidden |
| `lore journal read` | `lore_cli/journal_cmd.py` | none | none | hidden |
| `lore journal enable` | `lore_cli/journal_cmd.py` | none | none | hidden |
| `lore journal disable` | `lore_cli/journal_cmd.py` | none | none | hidden |
| `lore journal status` | `lore_cli/journal_cmd.py` | none | none | hidden |
| `lore ingest` | `lore_cli/ingest_cmd.py` | `README.md:268` · docs | 4 capture |  |
| `lore mcp` | `lore_cli/mcp_cmd.py` | `.claude-plugin/plugin.json:99` · manifest | 3 retrieval |  |
| `lore migrate frontmatter` | `lore_cli/migrate_cmd.py` | `README.md:396` (+1) · docs | 2 wiki notes |  |
| `lore migrate flag-blocks` | `lore_cli/migrate_cmd.py` | `README.md:277` · docs | none | flag residue |
| `lore quarantine list` | `lore_cli/quarantine_cmd.py` | none | none |  |
| `lore quarantine show` | `lore_cli/quarantine_cmd.py` | none | none |  |
| `lore quarantine clear` | `lore_cli/quarantine_cmd.py` | none | none |  |
| `lore quarantine kill` | `lore_cli/quarantine_cmd.py` | none | none |  |
| `lore scopes ls` | `lore_cli/scopes_cmd.py` | none | 2 wiki notes |  |
| `lore scopes show` | `lore_cli/scopes_cmd.py` | none | 2 wiki notes |  |
| `lore scopes rename` | `lore_cli/scopes_cmd.py` | `lib/lore_cli/attach_cmd.py:253` · code message | 2 wiki notes |  |
| `lore scopes reparent` | `lore_cli/scopes_cmd.py` | none | 2 wiki notes |  |
| `lore scopes reconcile` | `lore_cli/scopes_cmd.py` | none | 2 wiki notes |  |
| `lore scopes rm` | `lore_cli/scopes_cmd.py` | none | 2 wiki notes |  |
| `lore scopes wikis` | `lore_cli/scopes_cmd.py` | `README.md:273` (+1) · docs | 2 wiki notes |  |
| `lore scopes doctor` | `lore_cli/scopes_cmd.py` | `README.md:273` (+1) · docs | 2 wiki notes |  |
| `lore trace` | `lore_cli/trace_cmd.py` | `README.md:319` (+3) · docs | all four |  |
| `lore transcripts sync` | `lore_cli/transcripts_cmd.py` | `lib/lore_cli/spawn.py:285` · subprocess | 4 capture |  |
| `lore transcripts show` | `lore_cli/transcripts_cmd.py` | none | 4 capture |  |
| `lore workflow validate-roadmap` | `lore_cli/workflow_cmd.py` | `lore-workflow/skills/orchestrate-epic/SKILL.md:59` (+4) · skill | 1 artifact map |  |
| `lore workflow create-prd` | `lore_cli/workflow_cmd.py` | `lore-workflow/skills/domain-modeling/ADR-FORMAT.md:70` (+3) · skill | 1 artifact map |  |
| `lore workflow epic-policy` | `lore_cli/workflow_cmd.py` | `lore-workflow/skills/orchestrate-epic/SKILL.md:20` (+1) · skill | 1 artifact map |  |
| `lore workflow seed-lift` | `lore_cli/workflow_cmd.py` | `lore-workflow/skills/seed-epic/SKILL.md:43` · skill | 1 artifact map |  |
| `lore workflow parse-board` | `lore_cli/workflow_cmd.py` | `lore-workflow/skills/orchestrate-epic/SKILL.md:28` (+1) · skill | 1 artifact map |  |

## MCP tools

The twelve tools `lib/lore_mcp/server.py` registers.

| Tool | Last caller | Job | Note |
| --- | --- | --- | --- |
| `lore_search` | `lore-workflow/skills/orient/SKILL.md:35` (+8) · skill | 3 retrieval |  |
| `lore_read` | `lore-workflow/skills/seed-epic/SKILL.md:46` (+1) · skill | 3 retrieval |  |
| `lore_drill` | `lore-workflow/skills/orient/SKILL.md:35` (+3) · skill | 3 retrieval |  |
| `lore_inbox_classify` | `skills/inbox/SKILL.md:4` · skill | 2 wiki notes |  |
| `lore_journal_write` | `lib/lore_cli/hooks.py:418` · code message | none | offered only when the journal is on |
| `lore_pending_verdicts` | `skills/verify/SKILL.md:69` · skill | 3 retrieval | freshness |
| `lore_verdict` | `skills/verify/SKILL.md:70` · skill | 3 retrieval | freshness |
| `lore_repo_docs_list` | `lore-workflow/skills/orient/SKILL.md:28` (+1) · skill | 1 artifact map |  |
| `lore_repo_docs_fetch` | `lore-workflow/skills/orient/SKILL.md:28` (+1) · skill | 1 artifact map |  |
| `lore_tier_resolve` | `docs/model-tiers.md:51` · docs | 3 retrieval | skills call `lore tier resolve` instead |
| `lore_codemap` | `lore-workflow/skills/brief/SKILL.md:26` (+5) · skill | 3 retrieval |  |
| `lore_context_pack` | `lore-workflow/skills/brief/SKILL.md:26` (+5) · skill | 3 retrieval |  |

## Config keys

Sources: user config is `~/.config/lore/config.yml`, root config is
`$LORE_ROOT/.lore/config.yml`, wiki config is `.lore-wiki.yml`, and `.lore.yml` is
the repo offer.

| Key | Source | Reader | Job | Note |
| --- | --- | --- | --- | --- |
| `lore_root` | user config | `lib/lore_core/config.py:113` · read | all four |  |
| `observability.hook_events.max_size_mb` | root config | `lib/lore_core/janitor.py:107` · read | all four |  |
| `observability.hook_events.keep_rotations` | root config | none | all four | no reader |
| `observability.runs.keep` | root config | `lib/lore_core/janitor.py:128` · read | all four |  |
| `observability.runs.max_total_mb` | root config | `lib/lore_core/janitor.py:129` · read | all four |  |
| `observability.runs.keep_trace` | root config | `lib/lore_core/janitor.py:130` · read | all four |  |
| `observability.retention.hot_days` | root config | `lib/lore_core/janitor.py:106` · read | all four |  |
| `observability.retention.cold_days` | root config | `lib/lore_core/janitor.py:152` · read | all four |  |
| `observability.retention.cold_max_mb` | root config | `lib/lore_core/janitor.py:153` · read | all four |  |
| `observability.retention.crash_log_days` | root config | `lib/lore_cli/_janitor_entry.py:31` · read | all four |  |
| `observability.proc.keep_generations` | root config | none | 4 capture | `spawn.py` hardcodes `keep=3` |
| `journal.enabled` | root config | `lib/lore_core/journal.py:79` · read | none | parked feature |
| `tiers.overrides` | root config | `lib/lore_core/tiers/__init__.py:69` · read | 3 retrieval |  |
| `user.display_name` | root config | `lib/lore_core/identity.py:88` · read | all four |  |
| `feedback.retrieval_misses` | root config | `lore-workflow/skills/orient/SKILL.md:80` · skill | 3 retrieval |  |
| `feedback.retrieval_misses_repo` | root config | `lore-workflow/skills/orient/SKILL.md:81` · skill | 3 retrieval |  |
| `git.auto_commit` | wiki config | none | 2 wiki notes | `sync.md` says no reader |
| `git.auto_push` | wiki config | `lib/lore_core/session_start.py:606` · read | 2 wiki notes |  |
| `git.auto_pull` | wiki config | `lib/lore_core/session_start.py:575` · read | 2 wiki notes |  |
| `models.simple` | wiki config | none | none | LLM client retired |
| `models.middle` | wiki config | none | none | LLM client retired |
| `models.high` | wiki config | none | none | LLM client retired |
| `heartbeat.enabled` | wiki config | none | none | no heartbeat reads it |
| `heartbeat.cooldown_s` | wiki config | none | none | spawn uses its own default |
| `heartbeat.push_context` | wiki config | none | none |  |
| `breadcrumb.mode` | wiki config | `lib/lore_core/breadcrumb.py:122` · read | 2 wiki notes |  |
| `breadcrumb.scope_filter` | wiki config | none | 2 wiki notes | no reader |
| `wiki` | `.lore.yml` | `lib/lore_cli/attach_cmd.py:260` · read | 2 wiki notes |  |
| `scope` | `.lore.yml` | `lib/lore_cli/attach_cmd.py:261` · read | 2 wiki notes |  |
| `backend` | `.lore.yml` | `lib/lore_core/session.py:46` · read | 2 wiki notes | copied into a dict nothing reads |
| `wiki_source` | `.lore.yml` | `lib/lore_core/offer.py:24` · read | 2 wiki notes | part of the offer fingerprint |
| `schema_version` | `.lore.yml` | none | 2 wiki notes | parsed, never consulted |
| `inherit` | `.lore.yml` | `lib/lore_core/offer.py:141` · read | 2 wiki notes |  |
| `LORE_ROOT` | env | `lib/lore_core/config.py:145` · read | all four |  |
| `LORE_CACHE` | env | `lib/lore_search/fts.py:50` · read | all four |  |
| `LORE_CURATOR_MODE` | env | `lib/lore_cli/hooks.py:371` · read | 4 capture | set by `spawn.py:232` |
| `LORE_SUPPRESS_CAPTURE` | env | `lib/lore_cli/hooks.py:378` · read | 4 capture | set by the orchestrate skills |
| `LORE_AI_AUTHOR` | env | `lib/lore_core/journal.py:118` · read | none | journal only |
| `LORE_USER_HANDLE` | env | `lib/lore_core/journal.py:124` · read | none | journal only |
| `_LORE_STATUS_NOW` | env | `lib/lore_cli/status_cmd.py:76` · read | all four | test clock |

## Deletion candidates

Each row below names no caller and no retained job. The owner gates each one.

| Row | Kind | Lines | Other places to change on delete |
| --- | --- | --: | --- |
| `lore_core/publish_gate.py` | module | 397 | `tests/test_publish_gate.py`, `_shape.py`, `_withhold.py`; `CONTEXT.md:48` and `:160` |
| `lore_core/secrets_env.py` | module | 184 | `tests/test_secrets_env.py` |
| `lore journal write` | verb | — | all five `journal` verbs live in `lore_cli/journal_cmd.py` |
| `lore journal read` | verb | — | as above |
| `lore journal enable` | verb | — | as above |
| `lore journal disable` | verb | — | as above |
| `lore journal status` | verb | — | as above |
| `lore quarantine list` | verb | — | all four `quarantine` verbs live in `lore_cli/quarantine_cmd.py` |
| `lore quarantine show` | verb | — | as above |
| `lore quarantine clear` | verb | — | as above |
| `lore quarantine kill` | verb | — | as above |
| `models.simple` | wiki config key | — | `WikiConfig.models` in `wiki_config.py`; README "Per-wiki configuration"; `docs/architecture/config.md` |
| `models.middle` | wiki config key | — | as above |
| `models.high` | wiki config key | — | as above |
| `heartbeat.enabled` | wiki config key | — | `WikiConfig.heartbeat` in `wiki_config.py`; `docs/architecture/config.md:136` |
| `heartbeat.cooldown_s` | wiki config key | — | as above |
| `heartbeat.push_context` | wiki config key | — | as above |

### Rows that fall with a candidate

These rows name a caller, so they are not candidates by the rule above. Each
caller is itself a candidate or serves no job.

- `lore_core/redaction.py` (159 lines). Its only importer is
  `publish_gate.py:36`.
- `lore_core/quarantine.py` (207 lines) and `lore_cli/quarantine_cmd.py`
  (158 lines). `publish_gate.py` is the only code that writes a quarantine
  entry. The four `quarantine` verbs have no caller.
- `lore_cli/journal_cmd.py` (138 lines) and `lore_core/journal.py`
  (263 lines). The journal is a parked feature and off by default. The
  hidden verbs have no caller. The live callers are the session-start
  directive at `lib/lore_cli/hooks.py:408`, the `lore_journal_write` tool,
  the `journal.enabled` key, and `LORE_AI_AUTHOR` and `LORE_USER_HANDLE`.
  Delete them as one unit or keep them as one unit.

## Unclear

Judgement calls for the owner.

- **Rows with a job but no caller.** No docs page, skill or code path names
  these rows. A person may still use the verbs.
  - Verbs: `install upgrade`, `install reinstall`, `attach offer`,
    `attach attachments ls`, `attach attachments rm`,
    `attach attachments purge-unattached`, `search`, `project status`,
    `inbox classify`, `scopes ls`, `scopes show`, `scopes reparent`,
    `scopes reconcile`, `scopes rm`, `transcripts show`.
  - `lore search` duplicates the `lore_search` tool, which the skills call.
    `lore inbox classify` duplicates `lore_inbox_classify`, which
    `skills/inbox/SKILL.md` calls.
  - Keys: `observability.hook_events.keep_rotations`,
    `observability.proc.keep_generations`, `git.auto_commit`,
    `breadcrumb.scope_filter`, and `schema_version` in `.lore.yml`. Each is a
    dataclass field that no code reads. `spawn.py:86` fixes `keep=3` in code
    for the value that `keep_generations` names.
- **Session-note readers.** Since PRD 0013 nothing writes a session note.
  These rows still read one:
  - `lore_core/note_document.py` (215 lines), called by `trace.py:21` and
    `seed_epic.py:19`.
  - `lore_core/ref_verify.py` (207 lines). Only its three status constants
    reach `note_document.py:29`.
  - `lore workflow seed-lift` (`lore_workflow/seed_epic.py`). The seed-epic
    skill calls the verb. The verb returns nothing when no note exists, and
    the skill then writes the seed by hand.
  - `lore_core/templates/session.md`. `lore wiki new` copies the template
    into every new wiki.
- **`lore hook stop`.** `.claude-plugin/plugin.json:78` runs the verb on every
  Stop event. `_stop()` at `lib/lore_cli/hooks.py:110` returns an empty
  string. The manifest entry starts a process that does nothing.
- **`lore migrate flag-blocks`.** Flags are retired on this epic. Old wikis may
  still carry flag blocks, so the migration may need to outlive the flags.
  `README.md:277` documents it.
- **`backend` in `.lore.yml`.** `lib/lore_core/session.py:46` copies the value
  into the attach-block dict. No code reads `block["backend"]` after that.
- **The `## Lore` block in `CLAUDE.md`.** `read_attach` in
  `lore_core/attach.py` has no caller. `attach_cmd.py` imports only the
  section finder, to strip the legacy block. This repo's own `CLAUDE.md`
  still carries a `## Lore` block with `wiki`, `scope` and `backend`, and
  no code reads it.
- **Docs drift.** `docs/architecture/config.md` lists `LORE_TRACE_LLM`,
  `LORE_LOG_NOW`, `LORE_ASCII` and a `run_render.py`. No code reads those
  variables and the file does not exist.
- **`lore_tier_resolve`.** Only `docs/model-tiers.md:51` names the tool. The
  skills call `lore tier resolve` from a shell instead.
- **Job boundaries.**
  - Inbox, curator, lint and project notes are under job 2. They produce or
    maintain wiki notes; they do not inject them. The owner decides whether
    job 2 covers that upkeep.
  - Freshness, verdicts and stale markers are under job 3, because they rank
    wiki hits at retrieval. They also feed the session-start chip, so job 2
    fits as well.
  - `redaction.py` has no job today. Transcript capture could adopt it before
    the sync mirrors a transcript. The owner decides between adopt and delete.
