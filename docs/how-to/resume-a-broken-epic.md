# Resume a build run

**Goal:** continue a `build` run after a crash, a closed laptop, `/clear` or
a compaction, without redoing merged work.

Each `build` mode writes a resume line after every merged round or feature.
A resume line holds the state of the run and the next ask. The run continues
from the last one.

## Resume an epic

1. **Run `/lore-workflow:build` in `epic` mode on the same epic issue.** Use a
   fresh session.
2. **The skill reads the board.** It finds the status comment on the epic
   issue and pipes it to `lore workflow parse-board`. The parser reads the
   feature table and the `## Ledger` section. A malformed board fails with an
   error; the parser does not guess.
3. **The skill compares the board with the epic branch:**
   - a `merged` feature is done and does not run again;
   - a `queued` or `blocked` feature runs again;
   - an existing `epic/<issue>` branch is reused.
4. **The skill edits the same comment.** A resumed run opens no second status
   comment.

With no status comment on the epic, the run starts fresh.

## Resume a loop or an issue

The `loop` and `issue` modes keep the ledger in the worktree:
`$(git rev-parse --git-dir)/lore-ledger.md`.

1. **Open a session in the worktree.** `lore hook session-start` finds the
   ledger and offers to resume from its last resume line.
2. **Accept the offer**, or run `/lore-workflow:build` in the same mode. The
   skill reads the last resume line and continues from its next ask.

The hook makes no offer for a finished run. `build` archives the ledger at
the finish point with `lore workflow ledger-archive`.

## Done when

The run reaches the same finish point a first pass reaches, with no feature
built twice.
