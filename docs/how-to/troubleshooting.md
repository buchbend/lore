# Troubleshooting: hooks, capture, and what lands in a wiki

**Goal:** find the cause of a specific symptom using the three
observability commands, without reading source. Background on *why*
these three commands exist and what they read: `lore trace`, `lore
status`, `lore doctor` are all covered in
[`docs/architecture/observability.md`](../architecture/observability.md).

## "Hooks aren't firing"

Escalate through these steps, each one level deeper:

1. **`lore status`** — check the `capture` section's `Hook` line. A
   timestamp within the last few minutes means hooks *are* firing; "no
   hook events" or a stale timestamp means they aren't.
2. **`lore doctor`** — the `hook_runnable` check actually invokes `lore
   hook session-start --plain` and reports whether it succeeds. If this
   fails, the problem is the `lore` binary or its environment, not
   Claude Code. Also check the plugin-cache-drift checks here — a stale
   Claude Code plugin cache silently keeps running old hook code even
   though `main` (or your installed version) has moved on; `--fix` can't
   repair this one, but the check names the exact drift.
3. **`lore status`'s news section** — if `doctor` passes but the ledger
   still doesn't grow, check for a recent `transcript-synced` entry there.
   Transcript sync's drain event lands on the shared system stream, not a
   per-session one, so this confirms sync ran at all rather than for one
   session in particular. Nothing there and a recent `Hook` timestamp
   means the hook fired but the detached sync never spawned — check
   `$CLAUDE_PROJECT_DIR` and that the plugin hooks are actually installed
   (`lore install`). `lore trace <session-id>` cannot help here: no
   current producer mints a `trace_id`, so it always reports no match.

## "Nothing appeared in the wiki after my session"

That is the normal case. Lore writes nothing into a wiki on its own: a
session leaves a transcript-ledger entry, and nothing else. A fact worth
keeping leaves the session as a repo artifact — an issue, a comment, or a
pull request on the wiki repo.

To confirm capture itself ran, `lore status` shows the last hook fire.
`lore doctor` checks the hook wiring. Neither depends on anything
having been written to a wiki.

## "My wiki still holds session notes under `sessions/`"

Those files are inert markdown. No Lore code reads them: `lore lint`
no longer walks the `sessions/` tree, `lore_context_pack` returns no
`sessions` key, and `lore briefing` gathers nothing from the directory.
Keep the files, move them, or delete them — Lore behaves the same either
way. Delete them with `git rm` if you want the tree quiet; the wiki's git
history keeps a copy.

A wiki that ran an older Lore also holds `sessions/_recent.txt`, the index
`lore lint` used to regenerate over those notes. Nothing writes or reads it
now, and nothing removes it. Delete it with the notes.

The three migration verbs that used to help here are gone:
`lore migrate retire-session-notes`, `lore migrate open-items`, and the
older `lore curator --migrate-open-items`. The transcript-ledger backfill
that `retire-session-notes` ran has no replacement. A ledger entry that
carries no linkage block keeps none.

## A `queued` or `running` flush record on disk

`.lore/flushes/` can still hold records left over from before the compose
pipeline retired. No code reads them — `lore_core/flush_store.py` is
deleted and no code advances a record. The retention janitor deletes the
whole `.lore/flushes/` directory on its next opportunistic pass, so a
`queued` or `running` record there is a leftover, not a flush stuck
mid-flight. Delete the directory by hand if you want it gone sooner.

## Known rough edges (honest, not yet fixed)

- **LLM prompt/response text isn't persisted** in run events — only
  metadata (model, token count, latency) is kept, to stay well under the
  spine's `PIPE_BUF` atomicity budget. To see the actual prompts/responses
  for a specific run, re-run with `LORE_TRACE_LLM=1` set and watch the
  live output.
- **`lore doctor --fix --json` interleaves human-readable repair
  prompts/receipts before the JSON envelope** on stdout — the repairs run
  and print ahead of the `if json_out` branch. Script against `lore
  doctor --json` (without `--fix`), or pass `--fix --yes` and parse only
  the trailing JSON line.

## "No such command" for `lore log` / `lore news` / `lore runs` / `lore proc`

These are gone, not renamed under a flag — `lore trace` and `lore status`
fully absorbed their role (see `CHANGELOG.md`'s `### Removed` entry). Reach
for `lore trace <selector>` for the correlated, chronological story these
used to print, or `lore status` for the health snapshot.

Other verbs that moved rather than vanished:

| You typed | Use instead |
| :--- | :--- |
| `lore detach` | `lore attach remove` |
| `lore attachments <sub>` | `lore attach attachments <sub>` |
| `lore registry ls` / `lore registry doctor` | `lore scopes wikis` / `lore scopes doctor` |
| `lore migrate --add-schema-version` | `lore migrate frontmatter --add-schema-version` |

`lore migrate open-items` and `lore migrate retire-session-notes` are not
in that table — both were removed outright, with nothing to move to. See
["My wiki still holds session notes under `sessions/`"](#my-wiki-still-holds-session-notes-under-sessions).

`lore journal` still works; it is only hidden from `lore --help` while the
feature stays parked.

## "No such command: completions" — shell completion

`lore completions <shell>` is gone. Shell completion now comes from Typer's
native flags on the root command, which cover every verb including the
folded ones:

```bash
lore --install-completion     # install into your shell's rc
lore --show-completion        # print the script, to source or inspect
```

If your shell rc carries `eval "$(lore completions bash)"` from an earlier
version, that line now fails on startup. Replace it — `lore
--install-completion` writes the wiring for you, so no `eval` line is needed
at all.
