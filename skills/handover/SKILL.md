---
name: lore:handover
description: Write a handover note so the next session continues this one after /clear or compaction. SessionStart injects the note automatically. Use when the user says "handover", "hand over", "write a handover", or before they run /clear on unfinished work. Run with "/lore:handover".
user_invocable: true
---

# Handover — carry this session's state across /clear

`orient` rebuilds understanding from the repo. A handover carries what the
repo does not hold: decisions, dead ends, the next ask. SessionStart injects
the note in full after `/clear` or compaction, and offers it in one line on
a fresh start. Nothing runs before `/clear`, so write the note first.

## Write the note

Draft it in these sections. Keep empty sections with "none".

```md
## Goal
What the work is for, in one or two sentences.

## Decisions
Each decision made this session, and why. Include what was rejected.

## Done
Commits, PRs, issues filed — with ids and branch names.

## In flight
Uncommitted changes, running jobs, worktree paths, branches.

## Next
The next ask, as the first thing the new session does.

## Open questions
Questions for the user that are not answered yet.

## Where to look
Files, commands and URLs the next session needs first.

## Traps
What failed and why, so the next session does not repeat it.
```

Rules:

- Run `lore style show writing-rules` first; the note follows them.
- State facts with their source: path, commit, run id, log.
- Write for a reader with no memory of this session.
- Stay under the limit `lore handover write` enforces. Cut history, keep state.

## Store it

Pipe the note to `lore handover write` with a quoted heredoc:

```bash
lore handover write <<'EOF'
## Goal
...
EOF
```

A new note replaces the old one for this directory. Then tell the user:

- the note is stored and the path `lore handover write` printed;
- `/clear` now starts a session that receives the note;
- `lore handover show` prints it at any time.

## Do not

- Paste the transcript or long tool output into the note.
- Write a note for another directory; the note belongs to the current one.
