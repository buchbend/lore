# File a flag, and review the flags agents filed

**Goal:** get one team-relevant fact out of a working session and onto the
team wiki, then walk what landed and keep or drop each item.

A flag is one fact with a lead sentence, a short body and a stamped origin
line. Lore appends it to the owning topic note the moment it is filed. An
agent files flags through the `lore_flag` MCP tool; you file your own with
`lore flag write`.

The flag is the only crossing from a session to the team wiki —
[why the flag is the crossing](../explanation/why-the-flag-is-the-crossing.md)
explains the target state and what is still outstanding.

## Before you start

- A wiki that `lore scopes wikis` lists, and a repo attached to it.
- Run every command below from inside the attached repo, or pass `--wiki
  <name>`. Without either, `lore flag` exits 2 and tells you so.

## File a flag yourself

```
lore flag write "The reaper starves mid-drain when two sessions race the lock." \
  --body "The loser never retried, so the second buffer sat until the next boundary." \
  --ref pr:357 --ref commit:3f9a2c1
```

Rules the write path enforces:

- **No origin, no flag.** Pass at least one `--ref`, or a `--transcript`
  id. Inside a Claude Code session `$CLAUDE_SESSION_ID` supplies the
  transcript automatically. A plain shell has no session id, so pass a
  `--ref` there. A write with neither exits 1.
- **`--ref` takes `TYPE:VALUE`** — `pr`, `issue`, `commit`, `file` or
  `tag`. Repeat it per ref. Lore verifies each ref against the repo at
  write time. A ref it cannot check keeps session-talk phrasing; a ref
  that does not exist demotes the whole origin line.
- **Your words stay your words.** A flag written from your shell is
  human-authored: it lands without the unreviewed marker and skips the
  code-stamped phrasing. Pass `--agent` when you file on a model's
  behalf, and the flag lands stamped and unreviewed.
- **`--json`** prints the `lore.flag.write/1` envelope for scripting.

### Choose where it lands

Name the note with `--target`, as a wiki-relative path (`concepts/reaper.md`)
or a bare slug (`reaper`). Lore refuses a target that resolves outside the
wiki.

Without `--target`, Lore ranks the wiki with the ordinary search backend and
picks the top-ranked existing note — a flag lands where a reader searching
the same words would look. Session notes are never a target. When nothing
fits, Lore creates a new topic note under `concepts/` named after the lead.
The command prints `created` or `appended to` with the path either way.

### When the gate withholds a flag

The publish gate reads the flag text before anything else touches it, and it
fails closed. A withheld write exits 1 and prints a quarantine id:

```
withheld (secret) — text held in quarantine 8f2c1a4b9de0;
`lore quarantine show 8f2c1a4b9de0`
```

Nothing reached the wiki. Read the held text, rewrite the fact without the
material that tripped the gate, and file it again.

## See what is pending

```
lore flag list
```

One line per unreviewed flag: id, note, lead. The SessionStart banner shows
the same thing as a bare count — `· 3 pending flags` — and never the text of
a flag.

## Walk the review

```
lore flag review
```

Lore snapshots the pending list, then shows one flag at a time with its full
block and prompts `accept / retarget / decline / skip? [a/r/d/s]`. The
default is skip, so pressing Enter through the walk changes nothing.

| Verdict | What Lore does | Still pending after? |
|---|---|---|
| `a` accept | Removes the unreviewed marker. Nothing else in the note changes. | No |
| `r` retarget | Prompts for a note, moves the block there, creates the note when it is missing. | Yes |
| `d` decline | Deletes the flag block. The note's own prose is untouched. | No |
| `s` skip | Nothing. | Yes |

Retarget corrects where a flag lives, not whether you endorse its text, so
the marker stays and the flag returns in the next walk. A target you mistype
loses that one verdict and prints an error; the walk continues.

Declined flags leave the wiki but stay in its git history. Recover one with
`git log -p` on the note.

## Check the counters

- `lore status` — the `flags` section, per wiki: written, withheld,
  pending, accepted, declined, retargeted.
- `lore trace flag` — every flag-write and flag-review event,
  chronologically, each carrying a `flag_id`. `--json` gives raw records.

Pair one flag's write and verdict lines by `flag_id` to get its review
latency. [Measure flag quality](measure-flag-quality.md) covers what those
numbers can and cannot tell you, and the two procedures that catch
under-flagging.

## Done when

`lore flag list` prints `(no pending flags)`, and every fact you kept sits in
a topic note whose origin line no longer ends in `unreviewed`.
