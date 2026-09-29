# Measure token use

**Goal:** see how many tokens a Claude Code session spent, split by the skill or
subagent that spent them.

## Before you start

- `lore` installed.
- The session's transcript on disk: a session id, or the path to its `.jsonl`
  file.

## Steps

1. **Run the command with the session id or the path.**

   ```
   lore trace tokens <session-id>
   lore trace tokens ~/.claude/projects/<project>/<session-id>.jsonl
   ```

   A session id resolves through the transcript ledger, then through
   `~/.claude/projects/*/<session-id>.jsonl`. The command exits 1 when it finds
   no transcript. It only reads; it never writes.

2. **Read the table.** One row per phase, then a total:

   ```
   phase               messages  input  output  cache read  cache write  total
   main                       2     11       7         103           24    145
   skill:tdd                  2     22      12         202            2    238
   agent:explore repo         2     45      25         405            5    480
   all                                                                    1270
   ```

   - `main` holds the tokens before the first phase.
   - `skill:<name>` starts at each `Skill` tool call.
   - `agent:<description>` starts at each `Task` or `Agent` tool call. When the
     transcript has a `subagents/` folder next to it, each subagent's own usage
     lands in its agent row.
   - Each phase start makes a new row, even when a name repeats.
   - Each assistant message counts once, in the phase active when it began.

3. **Get JSON instead.** Add `--json` for `{"session", "phases", "total"}` on one
   line, for a script or a comparison between runs.

## Related

- [Model tiers](../model-tiers.md): which tier a skill runs at.
