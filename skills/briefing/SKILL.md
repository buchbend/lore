---
name: lore:briefing
description: Generate a developer briefing from a wiki's session notes
  and publish it via a configured sink (Matrix, Slack, Discord, markdown,
  GitHub Discussion). Calls MCP `lore_briefing_gather` for the
  deterministic part; LLM only composes prose. Run with
  "/lore:briefing <wiki>".
user_invocable: true
---

# Developer Briefing

Generates a concise briefing from one wiki's new session notes and
publishes it via the wiki's configured sink. The deterministic work
(reading the ledger, scanning sessions, parsing frontmatter, loading
sink config) is one MCP call. The LLM only composes the prose. Side
effects (publish + ledger update) go through visible Bash calls.

## Workflow — three tool calls minimum

### 1. MCP gather (silent, fast)

Call `mcp__lore__lore_briefing_gather` with:

```
{"wiki": "<name>"}
```

The tool returns:
- `wiki`, `today`
- `ledger`: `{last_briefing, incorporated_count}`
- `sink_config`: parsed `.lore-briefing.yml` (or null)
- `new_sessions`: list of `{path, date, slug, frontmatter, sections}`
  where `sections` maps H2 heading → body text per session note

If `new_sessions` is empty, report "No new sessions since last
briefing" and stop. **Do not** Glob the sessions dir yourself.

### 2. Compose the prose (LLM judgment)

Aggregate "What we worked on" across new sessions. Group by
project/domain, not chronologically. Deduplicate overlapping work.
List key decisions with their *why*. Merge open items / loose ends;
flag items repeated across sessions.

Target shape (≤30 lines regardless of how many sessions):

```
## Briefing: <today> (<wiki>)

### What happened
- **<project>**: <summary>

### Key decisions
- <decision and why>

### Open items
- <item>

### Vault health
- <N notes covered, M decisions, K open items>
```

### 3. Publish via Bash (visible side effect)

Pipe the composed prose through `lore briefing publish`:

```bash
lore briefing publish --sink <name> [--out <path>] <<'EOF'
<your composed briefing>
EOF
```

Sink name comes from `sink_config.sink` (or pass `markdown` with
`--out` if no sink is configured — good for review-before-send). The
markdown sink also accepts `--out` containing the literal
`YYYY-MM-DD` (replaced at publish time).

If `sink_config` is null, **don't publish** — just show the prose to
the user and stop. Don't fabricate a sink.

### 4. Mark incorporated via Bash

Once published, update the ledger so these sessions don't appear in
the next briefing:

```bash
lore briefing mark --wiki <name> \
    --session 2026-04-15-fix-a.md \
    --session 2026-04-16-fix-b.md \
    [...]
```

### 5. (Optional) Commit the ledger

```bash
git -C $LORE_ROOT/wiki/<name> add .briefing-ledger.json
git -C $LORE_ROOT/wiki/<name> commit -m "lore: briefing ledger <today>"
```

(Or run `lore session commit <ledger-path>` — the commit subcommand
works for any path inside a wiki, not just session notes.)

## Hard rules

- **One MCP call for gather.** No Glob, no Read. The tool returns
  parsed sections.
- **Always show the prose in the conversation**, even if publish
  fails — and report the error.
- **One wiki per briefing.** Different wikis have different audiences.
- **Sinkless wikis don't publish.** Show the briefing, stop. Don't
  invent a destination.
- **Credentials never enter the wiki repo.** `.lore-briefing.yml`
  only references env-var names; the sink adapters resolve actual
  values from `~/.config/lore/` or environment.

## Related

- `/lore:context` — what SessionStart cached
- `/lore:resume` — fresh gather (different shape: per-topic / per-scope)
