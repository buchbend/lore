# Hand over a session across /clear

A long session carries decisions and next steps the repo does not hold.
`orient` rebuilds understanding from the repo; a handover note carries the rest.

## Write the note

1. Say "handover" or run `/lore:handover` before `/clear`.
2. The agent writes the note and stores it with `lore handover write`.
3. Run `/clear`.

The new session receives the full note in its SessionStart context.

## When the note is injected

| Session start | What SessionStart does |
|---------------|------------------------|
| after `/clear` | injects the full note, then archives it |
| after compaction | injects the full note and keeps it |
| fresh start or `claude --continue` | shows one line: `run lore handover show to load it` |

Before compaction, the PreCompact hook reminds the agent to rewrite a note
whose state has moved on.

## Where the note lives

- One note per working directory: `$LORE_CACHE/handovers/<dirname>-<hash>.md`.
  `$LORE_CACHE` defaults to `~/.cache/lore`.
- Each worktree has its own directory, so parallel sessions keep separate notes.
- A new note replaces the old one. Archived notes end in `.done.md`.
- `lore handover write` rejects a note over 12,000 characters.

## Commands

```bash
lore handover write < note.md   # store a note for the current directory
lore handover show              # print it
```
