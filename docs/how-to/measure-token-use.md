# Measure token use

**Goal:** see how many tokens your Claude Code sessions spent, which models
served them, and what the use would cost at API list prices.

## Before you start

- `lore` installed, with the plugin's hooks active.
- For one session: its session id, or the path to its `.jsonl` transcript.

Lore records every session by itself. At `SessionEnd` the `lore hook
token-usage` hook counts the transcript and appends one token record to
`<lore_root>/.lore/token-usage.jsonl`. The file stays on your machine. While
`/lore:off all` mutes a session, the hook records nothing for it.

## Sum the recorded sessions

1. **Run the command with no session.**

   ```
   lore trace tokens
   lore trace tokens --days 7
   lore trace tokens --days 0
   ```

   `--days` sets the window. The default is 30 days; `0` sums every record.

2. **Read the three tables.**

   - `by model` groups the tokens by served model, the model that answered.
   - `by phase` groups them into `main`, one row per `skill:<name>`, and
     `agents` for every subagent.
   - `subagents by requested model` groups subagent tokens by the model and
     effort that each spawn asked for.

   Each table has an `est. USD` column (US dollars) and a `share` of the total cost.

3. **Check the mismatch list.** When a subagent asked for one model and
   another model answered, the command lists the session, the phase, the
   requested model and the served model.

4. **Get JSON instead.** Add `--json` for the same totals on one line.

`lore status` shows the last 7 days in its `tokens` section. The section
adds a warning line when a subagent ran on a model it did not ask for.

## Read the cost estimate

- The estimate uses API list prices, dated in the command output. The estimate
  is not a bill. On a Claude subscription, read it as "what this use would cost on the
  API".
- The table is in `lib/lore_core/token_prices.py`. One rate applies per model.
  The estimate leaves out long-prompt surcharges, fast mode and batch discounts.
- A cache write costs 1.25 times the input price for the 5-minute cache. It
  costs 2 times the input price for the 1-hour cache.
- A model without a price shows `*` next to its cost, and the command names it.

To change a price or add a model, set `tokens.prices` in
`~/.config/lore/config.yml`. The values are US dollars per million tokens:

```yaml
tokens:
  prices:
    claude-opus-5-5:
      input: 4
      output: 20
      cache_read: 0.2
```

## Look at one session

1. **Run the command with the session id or the path.**

   ```
   lore trace tokens <session-id>
   lore trace tokens ~/.claude/projects/<project>/<session-id>.jsonl
   ```

   A session id resolves through the transcript index, then through
   `~/.claude/projects/*/<session-id>.jsonl`. The command exits 1 when it finds
   no transcript. It only reads; it never writes.

2. **Read the table.** One row per phase, then a total:

   ```
   phase         model       messages  input  output  cache read  cache write  total
   main          opus-5-5           2     11       7         103           24    145
   skill:tdd     opus-5-5           2     22      12         202            2    238
   agent:review  sonnet-5-5         2     45      25         405            5    480
   all                                                                          863
   ```

   - `main` holds the tokens before the first phase.
   - `skill:<name>` starts at each `Skill` tool call.
   - `agent:<description>` starts at each `Task` or `Agent` tool call. When the
     transcript has a `subagents/` folder next to it, each subagent's own usage
     lands in its agent row.
   - `model` names the served models of the row. A transcript that names no
     model gets no `model` column.
   - Each phase start makes a new row, even when a name repeats.
   - Each assistant message counts once, in the phase active when it began.

   Under the table, the command lists each agent row whose served model
   differs from the requested model.

3. **Get JSON instead.** Add `--json` for `{"session", "phases", "total"}` on one
   line. Each phase carries a `models` split and, for a spawn that named one, a
   `requested` model and effort.

## Related

- [Model tiers](../model-tiers.md): which tier a skill runs at.
