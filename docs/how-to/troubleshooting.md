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

## "A note never appeared"

1. **`lore status`** — the alerts section fires when there's a
   dead-lettered flush or a diverged wiki; it names the exact
   drill-down command, so start here rather than guessing.
2. **Check the note itself for a marker chapter.** A mid-session failure
   is silent while a retry chance remains (by design — see
   [`session-note-lifecycle.md`, "How failures surface"](../architecture/session-note-lifecycle.md#how-failures-surface)),
   but a give-up or a publish-gate withhold leaves a **marker chapter**
   directly in the note. If the note exists and has a withheld-marker
   chapter, the actual content is sitting in `lore quarantine list` (safe
   category text only — a reviewer runs `lore quarantine show <id>` to
   see the redacted text).
3. **`lore trace dead`** — lists every dead-lettered flush (exhausted its
   3 retries) with its reason. If the buffer you expected shows up here,
   that's your answer: `data.error`/the reason string names the cause
   (spawn failure, compose failure, sidecar read error, chapter-append
   I/O error).
4. **`lore trace last`** (or a specific trace_id / note path /
   `[[wikilink]]` once you have one) — the full chronological story of
   that flush: hook decision, spawn, LLM calls, gate outcome, note
   append, with durations and error codes at each step. `--plain` for a
   script-safe rendering, `--json` for the raw spine records.

## "A flush looks stuck"

`lore status`'s `flushes` line shows `queued` / `running` / `dead-lettered`
counts. A flush sitting in `queued` or `running` for a long time is
either genuinely still working (spawn → compose → gate → append can take
tens of seconds) or waiting out its exponential backoff after a failed
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
- **A Popen-boundary spawn failure can lack a trace_id.** If the detached
  curator subprocess fails to start at the OS level, the
  `flush-spawn-failed` event is still written to the spine (so `lore
  status`/`lore doctor` see it), but it isn't attachable to the rest of
  that flush's story via `lore trace` — look for `event:
  flush-spawn-failed` directly in `lore trace <session-id>`'s output
  instead of expecting it under a specific trace_id.

## Deprecated commands still work, but check the pointer

`lore log`, `lore news`, `lore runs`, and `lore proc` still run during
their deprecation window — each prints a one-line pointer to its
replacement on stderr, then behaves exactly as before. If a script or
habit still reaches for one of these, the pointer names what to switch
to; they're removed on the version named in `CHANGELOG.md`'s
`### Deprecated` entry for issue #195.
