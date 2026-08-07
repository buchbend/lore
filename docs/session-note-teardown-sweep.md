# Session-note and curator residue — code sweep

Swept at `509c126` (0.69.0, current `main`). Method: AST symbol extraction across
`lib/`, then a reference search over every non-test file. Docstring-only and
comment-only mentions were excluded by hand. `CODEMAP.md` is a generated symbol
index and was excluded — it names every symbol and matches everything.

The first pass ran against the epic branch at `fa1d326`. Re-run after rebasing
onto `main`, every code finding was identical; the docs findings changed and are
corrected below. `main` now carries ADR 0010, which ratifies the producer rule
this sweep applies.

Test baseline before any change: 2667 passed, 2 failed, 38 skipped.
Both failures (`test_cli_scopes.py::test_rename_reports_stale_checked_in_offer`,
`test_install_dispatcher.py::test_refresh_claude_plugin_cache_runs_update_on_success`)
assert on ANSI-wrapped terminal output and are unrelated to session notes.

## Summary

| Category | Production LOC | Test LOC |
| --- | --- | --- |
| Dead — no production caller | ~1,540 | ~2,360 |
| Producerless — reads an artifact nothing writes | ~290 | — |
| Live but misnamed or misdocumented | — | — |

## 1. Dead — tests are the only importer

### `lib/lore_core/session_writer.py` — 324 LOC, 1 live symbol

The session-note body composer. `parse_body_sections`, `render_body_sections`,
`merge_body_sections`, `BodySections`, `FiledNote`, `_dedup_lines` have no
production caller. Only `session_path_sort_key` (39 LOC) survives, called by
`lore_core/lint.py:759`.

Move `session_path_sort_key` into `lint.py` and delete the file. Removes
`tests/test_session_writer.py` (292 LOC).

The module docstring also names `lore_curator.stub_note`,
`lore_curator.synthesis`, and `lore_curator.summary_merge`. None of those exist.

### `lib/lore_curator/session_activity.py` — 568 LOC, 3 live helpers

Built the session note's `## Activity` section. Dead public surface:
`CommitRef`, `collect_commits_by_sha`, `render_commits_section`,
`extract_issue_refs`, `collect_issues_in_window`, `render_issue_section`,
`collect_projects_for_session`, `_files_read_from_turns`,
`_files_touched_from_turns`, `_is_git_commit_command`,
`_project_slug_from_abs_path`, `_file_path_from_tool_input`. That is ~357 LOC.

`lore_core/linkage.py:10` and `:75` name `collect_commits_by_sha`, but both are
prose inside a docstring, not calls.

Live: `_all_turn_text`, `_commit_shas_from_bash_results`,
`_files_modified_from_turns` — ~92 LOC, imported by
`lore_curator/ledger_linkage.py:74`. Fold those three into `ledger_linkage.py`
and the module deletes entirely. Removes `tests/test_session_activity.py`
(459 LOC).

### `lib/lore_core/note_document.py` — 1084 LOC, ~5 live symbols

PRD 0012 put this module out of scope on the grounds that it "carries live
callers". It does, but only these:

- `DISCLAIMER`, `NoteView`, `read_note` — `lore_workflow/seed_epic.py:19`,
  `lore_core/trace.py:21`
- `append_marker_chapter`, `MARKER_WITHHELD` — `lore_core/publish_gate.py:389`
- `_ref_clause` — `lore_core/flag.py`

Everything else is the session-note chapter lifecycle with no caller:
`create_note`, `append_chapter`, `append_facts`, `close_note`, `reopen_note`,
`is_closed`, `read_facts`, `render_note`, `render_note_body`,
`render_chapter_body`, `render_fact_body`, `parse_facts`, `Chapter`,
`TopicBlock`, `Fact`, `Ref`, `SessionFacts`, `NoteClosedError`, and their
private helpers. That is ~700 LOC.

`SessionFacts` looked live but `lore_core/session_start.py:279` defines its own
class of the same name.

This is the single largest item in the sweep. Removes most of
`tests/test_note_document.py` (1442 LOC).

Note: ADR 0001 and ADR 0003 document `reopen_note` and `render_note`. Both
become historical record once the functions go.

### `lib/lore_core/flush_store.py` — 189 LOC, whole module

Its own docstring states the position: "No code opens a new record." The compose
pipeline (#361) was the only writer, and #377 removed the surviving write half.
What remains is a reader plus `FlushStore.purge`, called once from
`lore_core/janitor.py:124`.

The store empties itself over one retention horizon. After that horizon the
module and its purge call can go together. Removes `tests/test_flush_store.py`
(164 LOC).

### `lib/lore_core/identity.py::session_note_dir` — 10 LOC

Chooses `sessions/<handle>/` or `sessions/`. Nothing writes session notes, so
nothing calls it. The only reference is a docstring in `session_writer.py:19`.

`unaliased_authors` and `aliased_emails` are also test-only, but they are
identity concerns, not session-note ones.

## 2. Producerless — reads an artifact nothing writes

These still return real data for users who have historical session notes on
disk. For a fresh install they return empty forever. Removing them is the same
call PRD 0012 made on the other producerless surfaces, so it belongs to the
owner, not to a sweep.

### `lib/lore_core/context_pack.py` — sessions half, ~98 LOC

`_iter_session_notes`, `_matching_sessions`, `_session_matches`,
`_session_date_from_path` scan `<wiki>/sessions/**`. `gather` is live — the MCP
tool `lore_context_pack` calls it at `lore_mcp/server.py:1007` — but its
`sessions` key is now fed by a directory nothing fills.

`lore-workflow/skills/orient/SKILL.md:34` still advertises "the pack's
`sessions` (recent related session notes)" as prior art.

### `lib/lore_core/briefing/gather.py` — 261 LOC

`gather()` collects "new session notes since the last briefing" by walking
`<wiki>/sessions/`. PRD 0011 parks briefings and PRD 0012 keeps them parked, so
this is parked-and-producerless rather than dead.

### `lib/lore_core/lint.py` — session index generation

`generate_recent_txt` writes `sessions/_recent.txt` listing the last 20 session
notes, and `_build_report` prints "N session notes in `sessions/`". Both go
quiet on a vault with no session notes.

## 3. One-shot migrations — keep until users have run them

- `lib/lore_curator/retire_session_notes.py` (311 LOC) —
  `lore migrate retire-session-notes`, documented at
  `docs/how-to/retire-session-notes.md`. This is the tool that removes session
  notes from a user's vault. It must outlive the code it retires.
- `lib/lore_curator/open_items_migration.py` (236 LOC) —
  `lore migrate open-items`, lifts open items out of session-note bodies.

Both have a defined end of life: once the field has migrated, both they and
their tests go.

## 4. Live but misnamed or misdocumented

Nothing to delete here; these are stale claims that will mislead the next reader.

### The `lore_curator` package name

`lib/lore_curator/__init__.py:1` still reads "the session-note curator (Curator
A)" and names `lore_curator.session_curator.run_curator_a` as the entry point.
That module does not exist. What actually lives in the package now is an LLM
client, capture routing, frontmatter hygiene, and two migrations — none of which
curate a session note.

### Docstrings naming deleted modules

| File | Names |
| --- | --- |
| `lib/lore_cli/session_cmd.py:3` | `lore_curator/session_filer.py` |
| `lib/lore_core/session.py:5` | `lore_curator/session_filer.py` |
| `lib/lore_core/session_writer.py:5,9` | `stub_note`, `synthesis`, `summary_merge` |
| `lib/lore_core/git.py:34` | `lore_curator.session_filer` |
| `lib/lore_core/linkage.py:10,75` | `session_activity.collect_commits_by_sha` |
| `lib/lore_core/run_log.py:10` | `session_curator` as a live run role |
| `lib/lore_curator/session_activity.py:334` | `session_filer.py` |
| `CONTEXT-FORMAT.md:6,14` | `lore_curator/stub_note.py`, `stub_note._placeholder_title` |

`docs/architecture/sync.md` and `docs/architecture/observability.md` carried two
more. Both were corrected by #389, merged into `main` as part of #390, and the
observability text now reads as historical narrative. Neither needs work.

### `README.md`

Line 3 and line 190 still say session notes auto-extract from transcripts.
`CONTEXT.md:46` already carries the correct statement: "Lore writes no session
note."

### `lib/lore_cli/spawn.py` — 338 LOC for one role

The `SpawnRole` registry and the `spawn(role, ...)` entry point were built for
`curator_a`, `curator_b`, `curator_c`, and `transcripts`. `SPAWN_ROLES` now holds
one entry, `"transcripts"`, and the one caller is
`lore_cli/hooks.py:593`. The runaway gate, cooldown stamps, and log rotation are
worth keeping; the registry indirection around a single row is not.

### `skills/curator/SKILL.md:21`

Describes the implements-propagation pass in terms of "a session note's
`implements: <slug>`". The pass is frontmatter hygiene and works on any note.

## Suggested order

1. `note_document.py` lifecycle — biggest single win, ~700 LOC plus ~1400 test LOC.
2. `session_activity.py` — fold 3 helpers into `ledger_linkage.py`, delete module.
3. `session_writer.py` — move the sort key into `lint.py`, delete module.
4. `identity.session_note_dir`.
5. Docstrings, `README.md`, `lore_curator/__init__.py`.
6. `spawn.py` registry flattening.
7. `flush_store.py` — after one retention horizon.
8. Producerless readers (`context_pack` sessions, `briefing/gather`, `lint`
   session indexes) — owner's call, same shape as PRD 0012.
9. Migrations — last, once the field has moved.
