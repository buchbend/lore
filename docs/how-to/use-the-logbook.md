# Use the logbook

**Goal:** record one entry at every finish point and deliver it to your team's sinks.

A **logbook entry** says who worked on what, for one PR, issue, epic or
branch. A **sink** receives the entry (ADR 0017). The agent writes the entry
at the finish point of each build mode and when work stops early.

## Add a sink

The `file` sink writes `docs/logbook/YYYY-MM.md` in the repo:

```
lore setup sink file
```

A `command` sink runs a shell line. Add a secret when the command needs one:

```
lore setup sink command --name pm --run 'pm log add' --secret PM_TOKEN
```

Both commands write the definition to `~/.config/lore/config.yml`. They then
check, without posting, that the command is on `PATH` and that the secret is
set. A failed check exits 1 and keeps the definition. Store the secret with
`lore config secret set PM_TOKEN`.

A scope uses a sink once its `logbook` list names the sink:

```yaml
logbook: [file, pm]
```

`--scope NAME` writes that list for you. The command adds the sink to the
list of scope `NAME`. Where the scope has no list yet, the new list keeps the
sinks the scope used before: your `defaults.logbook`, or else `file`. An
unknown scope exits 1 and names the known scopes.

```
lore setup sink command --name pm --run '<shell line>' --scope ccat
```

## Write an entry

```
lore log add --ref <ref> --text-file entry.md
```

- `--ref` is a PR, an issue, an epic or `branch@sha`.
- `--text-file -` reads the entry from standard input.
- Lore adds the author, the date, the repo and the scope.
- The key of the entry is `lore:<owner/repo>:<ref>`.

The entry follows four rules. If the entry breaks one, the command exits 2
and names the rule:

- one paragraph
- at most 80 words
- at least one Markdown link
- no line starts with `todo:` or `decided:`

## Delivery

`lore log add` keeps the entry as a `[logbook]` line in the local ledger. It
then delivers the entry to every sink of the route in parallel. Each sink has
30 seconds. A sink that fails stays `undelivered` on the line.

- The `file` sink puts the newest entry first under a month heading. A repeated key writes nothing. The sink adds `docs/logbook/*.md merge=union` to `.gitattributes` once.
- The `command` sink reads the entry on standard input. It gets `LORE_ENTRY_KEY`, `LORE_ENTRY_AT`, `LORE_ENTRY_REPO`, `LORE_ENTRY_SCOPE` and `LORE_ENTRY_REF` in its environment. The secret arrives as `LORE_SECRET`. Exit code 0 means delivered.

## Retry and read

```
lore log flush
lore log show
```

- `flush` retries each undelivered sink. It reads the sink definitions again, so a fixed definition works on the next flush.
- `show` prints the entries of this month from the `file` sink.
- The SessionStart hook and the session-end hook start `lore log flush` in the background when an undelivered line exists.
- The SessionStart banner and the `route` block of `lore status` show the count of undelivered entries.
