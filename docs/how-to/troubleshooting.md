# Troubleshooting: hooks, missing flags, and capture

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

## "Nothing appeared in the wiki after my session"

That is the normal case. Lore writes nothing into a wiki on its own: a
session leaves a transcript-ledger entry, and a wiki note only when an
agent or a human filed a flag. If you expected a flag, see
["My flag never appeared in the wiki"](#my-flag-never-appeared-in-the-wiki).

To confirm capture itself ran, `lore status` shows the last hook fire and
the registered-transcript count per wiki. `lore doctor` checks the hook
wiring. Neither depends on anything having been written to a wiki.

## "A ref in my flag says `(unchecked)`"

The ref could not be checked, which is not the same as being wrong. Commits,
tags and files are verified against local git; pull requests and issues go to
`gh`. When any of that is unavailable — you're offline, `gh` isn't installed
or authenticated, the flag was written outside the repository — the check
cannot run, and the ref is stamped `(unchecked)`.

**Positive evidence only:** a check that could not run never promotes a ref and
never demotes it either. A failed `gh` call means GitHub was unreachable, not
that the PR is fake, so it can never render `(not found)` — otherwise an offline
laptop would rewrite history. Writing never fails on this; it only hedges. See
[ADR 0004](../adr/0004-authority-phrasing-is-code-stamped.md).

`(not found)` is the other verdict, and it means the opposite: a check *did*
run and came back empty. That ref does not exist, and its line is demoted to
"Claimed in session, ref not found: …".

## "My flag never appeared in the wiki"

Three refusals, each with its own message on stderr.

**`a flag needs an origin: pass a transcript pointer or at least one ref`** —
the write carried neither. Inside a Claude Code session
`$CLAUDE_SESSION_ID` supplies the transcript automatically; a plain shell has
no session id, so pass at least one `--ref pr:123` / `--ref commit:3f9a2c1`.
Exit code 1, nothing written.

**`no wiki resolved — pass --wiki, or run inside an attached repo`** — the
working directory maps to no attachment. Run `lore attach attachments show
$PWD` to see what covers the path, or name the wiki with `--wiki`. Exit code
2.

**`withheld (<category>) — text held in quarantine <id>`** — the publish gate
found a secret, an email address, a phone number or other personal data in
the flag text — or failed while checking — and withheld it before anything
touched the wiki. The gate fails closed, so a `gate-error` category means
the check itself broke, not that your text was bad. Read the
held text with `lore quarantine show <id>`, rewrite the fact without the
material that tripped the gate, and file it again. Exit code 1.

A flag that landed somewhere unexpected was routed, not lost: without
`--target`, Lore appends to the top-ranked existing note for the lead
sentence. `lore flag list` prints every pending flag with its note, and
`lore flag review` moves one with the retarget verdict.

## "The banner says N pending flags and I can't see them"

By design — the banner carries a count and never flag content, so a
teammate's unreviewed text cannot reach your context window through the
banner alone. Run `lore flag list` for the leads, or `lore flag review` to
walk them. The chip disappears at zero.

If the count looks wrong, remember it is a live scan of the wiki's notes for
the `unreviewed` marker, not a stored queue. Editing a marker out of a note
by hand accepts that flag as far as the count is concerned, and records no
`flag-review` event — which is why `lore status`'s pending count and its
accept/decline counters are not expected to sum. See
[measure flag quality](measure-flag-quality.md).

## "A flush looks stuck"

`lore status`'s `flushes` line shows `queued` / `running` / `dead-lettered`
counts. A flush sitting in `queued` or `running` for a long time is
either genuinely still working or waiting out its
exponential backoff after a failed
attempt (base 60s, doubling, capped at 3600s — see
`lore_core/flush_store.py`). `lore trace <selector>` shows which: a
`flush-running` step with no `flush-published`/`flush-dead-lettered`
after it is legitimately in-flight. If it's been longer than the retry cap and nothing moved, run
`lore doctor` — a corrupted flush record is one of the `--fix`-repairable
states.

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
