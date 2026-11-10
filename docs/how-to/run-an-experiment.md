# Run an experiment

**Goal:** run one experiment and record it as one lab entry.

A **lab entry** is the dated record of one experiment (ADR 0019, the lab
notebook). It has five labelled parts and a stamp that Lore writes. A
**thread** is the lab entries that follow one idea. The `experiment` skill
of `lore-workflow` walks the steps below. You can also run `lore lab add`
yourself.

The example measures the drift of a local oscillator over six hours.

## 1. Write the question and the expected result

Write both before the run, so the entry shows whether the result was a
surprise:

- **Question:** does the drift stay under 0.1 K per hour?
- **Expected:** about 0.05 K per hour, as with the old bias board.

Read the earlier entries of the thread first:

```
lore lab show --thread lo-drift
```

## 2. Run the experiment

```
python scripts/drift.py --hours 6 --out data/drift-2026-11-10.csv
```

Keep the command line and the data path for the entry. An entry links to a
plot or a binary result by path; the entry does not hold it.

## 3. Write the entry

Write `entry.md` in five labelled parts, at most 400 words:

```markdown
**Question:** Does the drift stay under 0.1 K per hour?
**Setup:** Cold load for 6 h, 1 s sampling, new bias board.
**Result:** Drift 0.04 K per hour. We expected 0.05 K per hour.
**Interpretation:** The new bias board holds the specification.
**Next:** Repeat with the warm load.
```

Add the entry:

```
lore lab add --text-file entry.md \
  --cmd 'python scripts/drift.py --hours 6 --out data/drift-2026-11-10.csv' \
  --data data/drift-2026-11-10.csv --thread lo-drift --status concluded
```

- `lore lab add` rejects an entry without one of the five labels, and names
  the label. The command also rejects an entry with more than 400 words.
- Lore writes the stamp as YAML front matter: date, author, repo, commit,
  dirty flag, branch and host. `--cmd` adds the command line. `--data` adds
  the path and, for a readable file, its SHA-256. Repeat `--data` for more
  paths.
- `--status` is `open` (the default), `concluded` or `dead-end`. Use
  `dead-end` when the experiment failed to answer the question. A dead end
  in an experiment is a lab entry, not a closed issue.

## 4. Commit the entry file

The `file` sink writes one file per entry under `docs/lab/YYYY-MM/`. Commit
the file on the current branch:

```
git add docs/lab/
git commit -m "lab: does the drift stay under 0.1 K per hour?"
```

Do not edit an entry later. A later entry in the same thread corrects it.

## Send entries to other sinks

A scope lists its lab sinks in `lab.sinks` in `~/.config/lore/config.yml`.
Without the key, the list is `[file]`. The sinks are the logbook sinks
(see [use the logbook](use-the-logbook.md)); `lab.sinks` is a separate list
from the `logbook` list:

```yaml
scopes:
  ccat:
    path: ~/git/ccat
    lab:
      sinks: [file, pm]
```

A sink that does not confirm delivery leaves the entry as a `[lab]` line in
the local ledger. The SessionStart hook and the session-end hook retry the
line with the logbook lines. `lore log flush` retries it at once.

## Read the entries

- `lore lab show` prints every entry, newest first.
- `lore lab show --thread lo-drift` prints only the entries of one thread.
- `lore index` and the SessionStart banner list the three newest entries by
  date and question. The `AGENTS.md` block of `lore index --write` leaves
  them out, so a new entry does not make the block stale.
