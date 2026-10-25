# Measure token use

**Goal:** see how many tokens your Claude Code sessions spent and which models
served them. See which workflow stage and which epic or issue spent them, and
what the use would cost at API list prices.

## Before you start

- `lore` installed, with the plugin's hooks active.
- For one session: its session id, or the path to its `.jsonl` transcript.

Lore records every session by itself. At `SessionEnd` the `lore hook
token-usage` hook counts the transcript and appends one token record to
`<lore_root>/.lore/token-usage.jsonl`. The file stays on your machine. While
`/lore:off all` mutes a session, the hook records nothing for it.

## Open the statistics page

1. **Run the command.**

   ```
   lore stats
   lore stats --days 7
   lore stats --no-open
   ```

   `lore stats` writes `<lore_root>/.lore/stats.html` and opens it in your
   browser. `--no-open` only prints the path. `--days` sets the window. The
   default is 30 days; `0` sums every record.

2. **Read the page.** The page shows the estimated cost per day, then tables by
   served model, by workflow stage, by epic or issue, and by stage and requested
   model. The page lists each subagent that ran on a model it did not ask for,
   and the most expensive sessions.

The page is one static file with no script and no network request. The same
token records always give the same page.

## How Lore finds the stage and the epic

- **Stage.** Every spawn in the lore-workflow skills opens its description with
  a stage tag, such as `[build:review]` or `[build:epic-review]`.
  `lore-workflow/TIER-DELEGATION.md` lists the tags. A subagent without a tag
  counts under "untagged subagents". Work in the main session counts under its
  skill, such as `skill:lore-workflow:build`.
- **Teammate session.** A teammate that runs as its own session gets a brief
  whose first line is the tag and the epic: `[build:implement]
  owner/repo#510`. The whole session counts for that stage and epic.
- **Epic or issue.** A reference after the tag counts the spawn for that epic
  or issue. Without one, the spawn counts for the reference in the arguments of
  the skill that runs, such as `/lore-workflow:build epic owner/repo#510`. The
  reference stays in effect until another one appears in the session.
- After `/clear`, the session starts without a reference. Work there counts
  under "no epic or issue" until a skill names the epic again.

## Sum the recorded sessions in the terminal

1. **Run the command with no session.**

   ```
   lore trace tokens
   lore trace tokens --days 7
   lore trace tokens --days 0
   ```

   `--days` sets the window. The default is 30 days; `0` sums every record.

2. **Read the four tables.**

   - `by model` groups the tokens by served model, the model that answered.
   - `by stage` groups them by stage tag. Main-session work counts under
     `main` or `skill:<name>`.
   - `by epic or issue` groups them by reference, as `repo#n`.
   - `subagents by stage and requested model` groups subagent tokens by stage,
     and by the model and effort that each spawn asked for.

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
