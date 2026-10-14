# Use the logbook

**Goal:** record one entry at every finish point and deliver it to your team's sinks.

A **logbook entry** explains why work was done and what it means for the
team, for one PR, issue, epic or branch. A **sink** receives the entry (ADR 0017). The agent writes the entry
at the finish point of each build mode and when work stops early.

## Add a sink

The `file` sink writes one file per entry under `docs/logbook/YYYY-MM/` in the repo:

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
- The key of the entry is `lore:<owner/repo>:<ref>`. A ref in the same repo
  keys as the bare ref: `acme/widget#471` in `acme/widget` gives
  `lore:acme/widget:#471`. A repo without a remote uses the folder name of
  its main checkout, also in a linked worktree.

The entry has four labelled parts, each on its own line:

```markdown
**Why:** the problem or the need, and why now.

**What we did:** the approach, with at least one choice and its reason.

**Consequences:** who notices the change, and what is different for them.

**Not done yet:** follow-up issues the work filed, and things nobody checked yet.
```

`Why:` without the bold markers counts too. If the entry breaks a rule, the
command exits 2 and names the rule:

- at most 300 words
- at least one Markdown link
- no line starts with `todo:` or `decided:`
- a line starts with each of the four labels

## Delivery

`lore log add` keeps the entry as a `[logbook]` line in the local ledger. It
then delivers the entry to every sink of the route in parallel. Each sink has
30 seconds. A sink that fails stays `undelivered` on the line.

- The `file` sink writes each entry to its own file, so two branches never change one file. A repeated key in the same month writes nothing.
- The `command` sink reads the entry on standard input. It gets `LORE_ENTRY_KEY`, `LORE_ENTRY_AT`, `LORE_ENTRY_REPO`, `LORE_ENTRY_SCOPE` and `LORE_ENTRY_REF` in its environment. The secret arrives as `LORE_SECRET`. Exit code 0 means delivered.

## Retry and read

```
lore log flush
lore log show
```

- `flush` retries each undelivered sink. It reads the sink definitions again, so a fixed definition works on the next flush.
- `show` prints the entries of this month from the `file` sink, newest first. A monthly file from before Lore 0.88 follows them.
- The SessionStart hook and the session-end hook start `lore log flush` in the background when an undelivered line exists.
- The SessionStart banner and the `route` block of `lore status` show the count of undelivered entries.
