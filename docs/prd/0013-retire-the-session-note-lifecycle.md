---
title: Retire the session-note lifecycle, its stock, and the curator framing
status: draft
epic: https://github.com/buchbend/lore/issues/TBD
repos:
  - buchbend/lore
---

# PRD 0013: Retire the session-note lifecycle, its stock, and the curator framing

> Source of truth for this epic. Tracker: [epic issue](https://github.com/buchbend/lore/issues/TBD).
> The epic links here; this file is not embedded in the issue body.

## Problem

ADR 0010 ratified the producer rule. A read surface without a live producer is a
defect. Issue #377 applied the rule to three enumerations: drain event kinds,
spine error codes, and status rows. `tests/test_producerless_surfaces_gone.py`
guards those three.

The session note itself was never enumerated. Issue #361 deleted the compose
pipeline in release 0.68.0. Nothing has written a session note since. The code
that composed, stored, indexed and read session notes still stands.

All observations below come from host `saiyajin`, at
`/home/buchbend/git/lore/.claude/worktrees/epic-375-teardown`, against
`origin/main` at `509c126`, release 0.69.0. The method was AST symbol extraction
across `lib/`, then a reference search over every non-test file. The full sweep
is recorded at `docs/session-note-teardown-sweep.md`.

### Four modules hold code that no caller reaches

- `lore_core/note_document.py` declares 45 symbols. Five carry a production
  caller. The session-note chapter lifecycle accounts for roughly 700 lines and
  no caller reaches it.
- `lore_curator/session_activity.py` spans 568 lines. Three private helpers
  carry a caller in `lore_curator/ledger_linkage.py`. Every public function is
  unreachable.
- `lore_core/session_writer.py` spans 324 lines. One function carries a caller.
- `lore_core/flush_store.py` spans 189 lines. Its own docstring records that no
  code opens a record.

Tests import each of these modules. The imports are the only reference. Roughly
2,000 lines of test code assert behaviour that no product path exercises.

### PRD 0012 read the caller count and drew the wrong conclusion

PRD 0012 held `note_document` out of scope because the module "carries live
callers". The module carries five. They are `read_note`, `NoteView`,
`DISCLAIMER`, `append_marker_chapter` and `_ref_clause`. None belongs to the
chapter lifecycle. The out-of-scope line hid the largest remaining target.

### Four readers walk a directory that nothing fills

- `lore_core/context_pack.py` scans `<wiki>/sessions/` and returns the matches
  under a `sessions` key. The MCP tool `lore_context_pack` serves that key.
- `lore_core/briefing/gather.py` collects "new session notes since the last
  briefing" from the same tree.
- `lore_core/lint.py` writes `sessions/_recent.txt` and reports a session-note
  count.
- `lore-workflow/skills/orient/SKILL.md` advertises the `sessions` key as prior
  art.

Each returns empty on an installation that never ran the retired pipeline.

### Two migrations outlive the artifact they migrate

`lore migrate retire-session-notes` deletes a user's session-note stock.
`lore migrate open-items` lifts open items out of session-note bodies. Together
they span 547 lines and 456 lines of tests.

### The package name states a role the code does not fill

`lore_curator/__init__.py` describes itself as "the session-note curator
(Curator A)". It names `lore_curator.session_curator.run_curator_a` as its entry
point. That module does not exist. The package now holds an LLM client, capture
routing, frontmatter hygiene and two migrations.

`lore_core/run_log.py` names `session_curator` among its live producers.
`lore_cli/spawn.py` spans 338 lines around a `SpawnRole` registry. `SPAWN_ROLES`
holds one entry. The registry served `curator_a`, `curator_b` and `curator_c`.

Eight further files name a deleted module in a docstring. A reader who follows
`lore_curator/session_filer.py`, `stub_note`, `synthesis` or `summary_merge`
finds nothing.

## Solution

Lore deletes every module whose only importer is a test. Lore deletes every
reader that walks the session-note tree. Lore deletes the migrations that
maintain a retired artifact. Lore corrects every name that states a role the
code does not fill.

After this epic:

- No module in `lib/` is imported only by its own tests.
- No code reads `<wiki>/sessions/`.
- The word "curator" names the frontmatter hygiene pass and nothing else.
- Every module a docstring names exists.
- The producer-rule guard test covers the session note.

## Implementation decisions

### The retained note-document surface

`note_document` keeps the symbols that carry a caller:

```
DISCLAIMER              # seed_epic, trace
NoteView, read_note     # seed_epic, trace
append_marker_chapter   # publish_gate
MARKER_WITHHELD         # publish_gate
_ref_clause             # flag
```

Lore deletes `create_note`, `append_chapter`, `append_facts`, `close_note`,
`reopen_note`, `is_closed`, `read_facts`, `render_note`, `render_note_body`,
`render_chapter_body`, `render_fact_body`, `parse_facts`, `Chapter`,
`TopicBlock`, `Fact`, `Ref`, `SessionFacts` and `NoteClosedError`.

`lore_core/session_start.py` declares its own `SessionFacts`. The two classes
share a name and nothing else. The deletion leaves `session_start` untouched.

### The three surviving activity helpers

`ledger_linkage` imports `_all_turn_text`, `_commit_shas_from_bash_results` and
`_files_modified_from_turns` from `session_activity`. Lore moves those three
into `ledger_linkage` and deletes `session_activity`.

### The session-note sort key

`lint.generate_recent_txt` is the one caller of
`session_writer.session_path_sort_key`. Lore deletes `generate_recent_txt`, so
the sort key loses its caller. Lore then deletes `session_writer` whole. No
symbol moves.

### The context pack

Lore removes the `sessions` key from `lore_context_pack`. The tool returns
`adr`, `prd` and `epic_state`. Lore removes the prior-art claim from
`orient/SKILL.md`.

An empty key that no producer fills is the defect ADR 0010 names. Preserving the
response shape would ship that defect.

### The flush records

Lore deletes `flush_store` and the janitor's purge call. The janitor removes
`.lore/flushes/` once, on its next run. A record already on disk otherwise
outlives every reader.

`FlushState` is local to `flush_store`. Issue #377 already dropped the spine
error codes that the flush lifecycle raised. The spine schema version therefore
holds at 2.

### The migrations

Lore deletes `retire_session_notes`, `open_items_migration`, both `lore migrate`
verbs, and `docs/how-to/retire-session-notes.md`.

The owner accepted the consequence. A user who never ran the migration keeps
session-note files as inert markdown. No code reads them. That user cannot
backfill transcript-ledger linkage from archived transcripts afterwards.

### The curator framing

`lore_curator` keeps its name. The package docstring states what the package
holds: an LLM client, capture routing, and frontmatter hygiene. Renaming the
package would touch every importer and buy nothing this epic needs.

`run_log` drops `session_curator` from its producer list. `spawn` drops the
`SpawnRole` registry and calls its one role directly. `spawn` keeps the runaway
gate, the cooldown stamps and the log rotation. Those answer a hang storm the
changelog records.

### The guard test

`tests/test_producerless_surfaces_gone.py` gains one assertion. No module under
`lib/` may resolve every importer to a test file. The assertion fails against
`509c126` and passes after the removals.

## Testing decisions

### Deletion is proved by absence

A test asserts that an import raises `ModuleNotFoundError`. A test asserts that
a symbol is absent from a surviving module. Prior art:
`tests/test_dead_code_gone.py` and `tests/test_producerless_surfaces_gone.py`.

### Behaviour over implementation

A test for `lore_context_pack` calls the tool and reads the returned keys. A
test for `lore lint` runs the command against a wiki holding a `sessions/`
directory and asserts that no `_recent.txt` appears.

### The retained callers keep their tests

`publish_gate` withholds a chapter through `append_marker_chapter`. `seed_epic`
and `trace` read a note through `read_note`. Each keeps its existing test. Those
tests are what prove the deletion cut at the right line.

Prior art: `tests/test_publish_gate_withhold.py`,
`tests/test_workflow_seed_epic.py`.

### Two failures predate this epic

`tests/test_cli_scopes.py::test_rename_reports_stale_checked_in_offer` and
`tests/test_install_dispatcher.py::test_refresh_claude_plugin_cache_runs_update_on_success`
fail against `509c126`. Both assert on ANSI-wrapped terminal output. Neither
touches this epic's scope. The baseline is 2667 passed, 2 failed, 38 skipped.

## Out of scope

- The transcript ledger. Every field it keeps carries a live reader.
- The flag path. PRD 0012 restored its transport and this epic does not touch it.
- Briefings. PRD 0011 parks them. This epic deletes the session-note walk inside
  `gather` and revives nothing.
- `lore_curator.llm_client`, `capture_routing`, `ledger_linkage` and `hygiene`.
  Each carries a live caller.
- The `lore curator` CLI verb and `skills/curator`. The hygiene pass is live.
  Only its session-note wording changes.
- Renaming the `lore_curator` package.
- ADR 0001 and ADR 0003. ADR 0007 already superseded both. Neither is rewritten.
- Issues #123 and #130. Both remain open under epic #131 on a human hold.
