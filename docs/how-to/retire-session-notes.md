# Retire the session-note stock into the transcript ledger

**Goal:** stamp every archived transcript with its linkage block, then delete
the session-note files those transcripts produced.

`lore migrate retire-session-notes` does both halves in that order. The
backfill is what makes the deletion safe to run: once each ledger entry
carries its repo, branch, PRs, issues, commits and files, `lore_drill`
answers "which sessions touched this?" and the SessionStart banner renders
its recap without reading a single note.

## Read this first

- **`--apply` deletes files.** Without it the command computes a plan,
  prints it, and writes nothing.
- **Deletion is final.** Capture writes no session note, so nothing refills
  the `sessions/` tree. Run this once, after upgrading past the teardown, to
  clear what earlier releases left behind.
- **Nothing distils the old notes.** The backfill reads the transcript and
  git, never the note prose. What a note said and no artifact records is
  lost when the file goes. Read anything you want to keep before you apply,
  and file it as a flag or write it into a topic note.
- **Recovery is git.** Notes are markdown in the wiki repo. Commit and push
  the wiki before you apply, and a mistaken run is a `git revert` away.

## Steps

1. **Commit the wiki.** From each wiki directory:

   ```
   git -C "$LORE_ROOT/wiki/<name>" status --short
   git -C "$LORE_ROOT/wiki/<name>" add -A && git -C "$LORE_ROOT/wiki/<name>" commit -m "wip: before session-note retirement"
   ```

   An uncommitted note that the run deletes has no history to recover from.

2. **Run the plan.**

   ```
   lore migrate retire-session-notes
   ```

   The output has two blocks:

   ```
   Backfill — 556 ledger entries (231 with issue/PR refs, 12 without a readable transcript)
   Delete — 307 session note(s)
     - /home/you/lore/wiki/private/sessions/2026/08/04-flag-lifecycle.md
     ...
     keep (not a note) /home/you/lore/wiki/private/sessions/2026/08/scratch.png
   ```

3. **Read the plan.** Three numbers are worth checking:

   - *entries with issue/PR refs* — how much of the archive keeps a usable
     ref after the notes go.
   - *without a readable transcript* — the transcript file is gone, so that
     entry's block holds only what git and the stored working directory
     could still answer.
   - *keep (not a note)* — non-markdown files found under a `sessions/`
     tree. The command leaves them in place.

4. **Apply.**

   ```
   lore migrate retire-session-notes --apply
   ```

   It executes exactly the plan it printed, then reports how many entries it
   backfilled and how many notes it deleted. Directories that empty out are
   pruned. Any file it could not delete is listed with its reason.

5. **Verify.** Ask the ledger something a note used to answer:

   ```
   lore drill "#358"
   ```

   `lore drill` prints an `N sessions:` block naming the transcripts that
   touched that issue. Start a new session and the banner's recap line reads
   off the same store.

## Scoping to one wiki

`--wiki <name>` scopes the **deletion** to one wiki. The backfill always
covers the whole ledger — it is one machine-local store, not a per-wiki one,
and stamping a linkage block adds data without removing any.

## What the backfill derives

Per archived transcript, from the transcript file and from git:

| Key | Source |
|---|---|
| `repo` | git, for the working directory the session ran in |
| `branch` | git, or the branch recorded in the transcript |
| `commits` | commit SHAs in the session's own bash results |
| `files` | files the session edited |
| `issues`, `prs` | refs in the turn text, classified by syntax |

No LLM call and no network call runs. A bare `#42` is classified by its
syntax rather than by asking GitHub what kind of ref it is, because
backfilling hundreds of sessions would otherwise mean a request per ref
inside a command that then deletes files.

The command is idempotent. A second run re-derives the same blocks and finds
whatever notes appeared since the first.

## Done when

`lore migrate retire-session-notes` reports zero deletions on a re-run
against a wiki you have already applied it to, and `lore drill` on a known
issue number returns the sessions that touched it.

## See also

- [Why the flag is the crossing](../explanation/why-the-flag-is-the-crossing.md)
  — what replaces the notes, and what the teardown still has to do.
- [`architecture/state.md`](../architecture/state.md) — where the ledger
  lives and why it never leaves the machine.
- [`architecture/lore-drill.md`](../architecture/lore-drill.md) — the ledger
  routing the backfill feeds.
