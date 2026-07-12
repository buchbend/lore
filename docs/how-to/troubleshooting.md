# Troubleshooting: hooks, missing notes, and stuck flushes

**Goal:** find the cause of a specific symptom using the three
observability commands, without reading source. Background on *why*
these three commands exist and what they read: `lore trace`, `lore
status`, `lore doctor` are all covered in
[`docs/architecture/observability.md`](../architecture/observability.md).

## "Hooks aren't firing"

Escalate through three commands, each one level deeper:

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
3. **`lore trace <session-id>`** — if `doctor` passes but notes still
   don't appear, trace the session directly (the session_id is what
   Claude Code shows in its own session info, or resolve it from
   `$CLAUDE_SESSION_ID` in the failing shell). No steps at all means the
   hook never ran; steps that stop after `hook/session-start` with no
   `curator/*` events means a flush was never spawned — check
   `$CLAUDE_PROJECT_DIR` and that the plugin hooks are actually installed
   (`lore install`).

## "No note appeared while my session was running"

**Expected — nothing is written until the session ends.** A session's turns
accumulate in a buffer; mid-session events (the buffer tripping its cap, a
pre-compact) only bookkeep. Which of a session's turns mattered is knowable
only backward, from its ending, so the whole session is read in one pass at
close and the note appears once, complete. `capture_routing.CLOSE_TRIGGERS`
is the single authority for which trigger flushes, and it holds exactly one
entry: `session-end`.

You will see a stub note file appear early (frontmatter, disclaimer, no
content) — that is the placeholder the close path fills in. If the session
has *ended* and the note is still empty or absent, that is the next entry.
Background: [why notes are written at session
end](../explanation/why-notes-are-written-at-session-end.md).

## "A note never appeared" (after the session ended)

1. **`lore status`** — the alerts section fires when there's a
   dead-lettered flush or a diverged wiki; it names the exact
   drill-down command, so start here rather than guessing.
2. **Check whether the session was discarded on purpose.** A trivial session
   (a handful of turns, no files touched, no commits, no issues) is dropped
   deterministically at close without spending a model call, and a session
   whose extraction returned zero facts is dropped too — the extractor's
   "nothing of substance" answer. Both delete the stub rather than close it
   around an empty note. `lore trace <session-id>` shows `flush-trivial` or
   `flush-empty` when this is what happened.
3. **Check the note itself for a coverage gap or a marker chapter.** A chunk
   the model could not extract becomes a **failed marker** in the ledger and a
   one-line coverage gap in the reading; a chunk the publish gate withheld
   becomes a **withheld marker**, and its actual content is sitting in `lore
   quarantine list` (safe category text only — a reviewer runs `lore quarantine
   show <id>` to see the redacted text). See
   [`session-note-lifecycle.md`, "How failures surface"](../architecture/session-note-lifecycle.md#how-failures-surface).
4. **`lore trace dead`** — lists every dead-lettered flush (exhausted its
   3 retries) with its reason. If the buffer you expected shows up here,
   that's your answer: `data.error`/the reason string names the cause
   (spawn failure, extraction failure, sidecar read error, chapter-append
   I/O error).
5. **`lore trace last`** (or a specific trace_id / note path /
   `[[wikilink]]` once you have one) — the full chronological story of
   that flush: hook decision, spawn, LLM calls, gate outcome, note
   append, with durations and error codes at each step. `--plain` for a
   script-safe rendering, `--json` for the raw spine records.

## "My note has a coverage gap line"

A line like *"Coverage gap: turns 40–71 are not covered by this note"* means the
rendered body cannot speak for that span of the session. Two causes:

- **A chunk failed extraction** (or the publish gate withheld it). The span is
  recorded in the ledger as a marker chapter with its reason, and the gap line
  carries that reason in parentheses. One bad chunk never costs the rest of the
  session — the other chunks still rendered.
- **The ledger holds legacy prose.** A note written before typed facts (or a
  session that reopened one) carries prose chapters, which contribute no facts.
  Their spans are gaps by construction. Old notes are not migrated — this is
  fix-forward.

The gap line is deliberate, not a bug: a partial note that presented itself as
complete would be worse than one that says where it stops. The transcript turns
are still there; `@N` anchors point into the archived transcript.

## "A ref in my note says `(unchecked)`"

The ref could not be checked, which is not the same as being wrong. Commits,
tags and files are verified against local git and the session's captured facts;
pull requests and issues go to `gh`. When any of that is unavailable — you're
offline, `gh` isn't installed or authenticated, the note was rendered outside
the repository — the check cannot run, and the ref is stamped `(unchecked)`.

**Positive evidence only:** a check that could not run never promotes a ref and
never demotes it either. A failed `gh` call means GitHub was unreachable, not
that the PR is fake, so it can never render `(not found)` — otherwise an offline
laptop would rewrite history. Rendering never fails on this; it only hedges. See
[ADR 0004](../adr/0004-authority-phrasing-is-code-stamped.md).

`(not found)` is the other verdict, and it means the opposite: a check *did*
run and came back empty. That ref does not exist, and its line is demoted to
"Claimed in session, ref not found: …".

## "A flush looks stuck"

`lore status`'s `flushes` line shows `queued` / `running` / `dead-lettered`
counts. A flush sitting in `queued` or `running` for a long time is
either genuinely still working (spawn → segment → extract → gate → render
can take tens of seconds, one model call per chunk) or waiting out its
exponential backoff after a failed
attempt (base 60s, doubling, capped at 3600s — see
`lore_core/flush_store.py`). `lore trace <selector>` shows which: a
`flush-running` step with no `flush-published`/`flush-dead-lettered`
after it, still within a plausible LLM-call duration, is legitimately
in-flight. If it's been longer than the retry cap and nothing moved, run
`lore doctor` — a corrupted flush record is one of the `--fix`-repairable
states.

## Known rough edges (honest, not yet fixed)

- **LLM prompt/response text isn't persisted** in run events — only
  metadata (model, token count, latency) is kept, to stay well under the
  spine's `PIPE_BUF` atomicity budget. To see the actual prompts/responses
  for a specific run, re-run with tracing on:
  `LORE_TRACE_LLM=1 lore curator run --dry-run` and watch the live
  output.
- **`lore doctor --fix --json` interleaves human-readable repair
  prompts/receipts before the JSON envelope** on stdout — the repairs run
  and print ahead of the `if json_out` branch. Script against `lore
  doctor --json` (without `--fix`), or pass `--fix --yes` and parse only
  the trailing JSON line.

## "No such command" for `lore log` / `lore news` / `lore runs` / `lore proc`

These are gone, not renamed under a flag — `lore trace` and `lore status`
fully absorbed their role (see `CHANGELOG.md`'s `### Removed` entry). Reach
for `lore trace <selector>` for the correlated flush story these used to
print, or `lore status` for the health snapshot.

Other verbs that moved rather than vanished:

| You typed | Use instead |
| :--- | :--- |
| `lore detach` | `lore attach remove` |
| `lore attachments <sub>` | `lore attach attachments <sub>` |
| `lore registry ls` / `lore registry doctor` | `lore scopes wikis` / `lore scopes doctor` |
| `lore curator backfill-slugs` | `lore migrate slugs` |
| `lore curator --migrate-open-items` | `lore migrate open-items` |
| `lore migrate --add-schema-version` | `lore migrate frontmatter --add-schema-version` |

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
