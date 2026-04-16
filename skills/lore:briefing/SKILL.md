---
name: lore:briefing
description: Generate a developer briefing from a wiki's session notes and
  publish it to a configured sink (Matrix, Slack, Discord, markdown,
  GitHub Discussion). Tracks which sessions have been incorporated to avoid
  duplicates. Run with "/lore:briefing <wiki>".
user_invocable: true
---

# Developer Briefing

Generates a concise briefing from **one wiki's session notes** and
publishes it to a configurable sink.

## Scope

Each invocation is scoped to one wiki. Briefings are not cross-wiki —
different wikis usually have different audiences.

## Configuration

Each wiki optionally declares a sink in
`$LORE_ROOT/wiki/<name>/.lore-briefing.yml`:

```yaml
# Matrix
sink: matrix
homeserver: https://matrix.example.org
room_id: "!abc:matrix.example.org"
# Credentials in ~/.local/share/lore/matrix-credentials.json

# Or Slack
sink: slack
webhook_url_env: SLACK_WEBHOOK_URL    # read from env, never committed

# Or Discord
sink: discord
webhook_url_env: DISCORD_WEBHOOK_URL

# Or plain markdown file (good for Obsidian, GitHub issues, etc.)
sink: markdown
path: "briefings/YYYY-MM-DD.md"       # relative to wiki root

# Or GitHub Discussion
sink: gh_discussion
repo: "org/name"
category: "Announcements"

# No file → conversation-only briefing
```

**Credentials never go in the wiki repo.** They live in `~/.config/lore/`
or per-sink state dirs, and adapter env vars reference them.

## Session ledger

`$LORE_ROOT/wiki/<name>/.briefing-ledger.json` tracks which sessions have
already been incorporated:

```json
{
  "last_briefing": "YYYY-MM-DD",
  "incorporated": ["YYYY-MM-DD-slug.md", ...]
}
```

Prevents the same session from appearing in multiple briefings.

## Workflow

### 0. Git pull the wiki

```bash
git -C $LORE_ROOT/wiki/<wiki> pull --ff-only
```

### 1. Check for new sessions

Read the ledger (create if missing). Glob `wiki/<wiki>/sessions/*.md`.
Filter out already-incorporated files. If nothing new → report "No new
sessions since last briefing" and stop.

### 2. Read new session notes

Parse frontmatter and body of each new session.

### 3. Generate briefing

**What happened** — aggregate "What we worked on" across new sessions.
Group by project/domain, not chronologically. Deduplicate overlapping
work.

**Decisions made** — list all decisions. Highest-value items.

**Open items** — merge open items; flag items that appeared in previous
sessions too (unresolved = higher priority). Cross-reference with the
wiki to check for resolutions.

**Vault health** — quick scan: any `status: stale`? Any `last_reviewed`
approaching 90 days?

### 4. Output format

```
## Briefing: YYYY-MM-DD (<wiki>)

### What happened
- **<project>**: <summary>

### Key decisions
- <decision and why>

### Open items
- ⚡ <item> (from YYYY-MM-DD)

### Vault health
- <N> notes, <M> reviewed within 90 days
```

Under ~30 lines regardless of how many sessions.

### 5. Publish via the configured sink

The sink adapter (Python module in `lore_sinks/`) handles transport:

```bash
python -m lore_sinks.<adapter> send --wiki <wiki> --file /tmp/lore/briefing.md
```

Available adapters: `matrix`, `slack`, `discord`, `markdown`,
`gh_discussion`. Sink-less wikis skip publish and show the briefing in
the conversation only.

If publishing fails, **still show the briefing in the conversation** and
report the error.

### 6. Update the ledger

Add new session filenames to `incorporated`. Set `last_briefing` to
today. Commit the ledger update:

```bash
git -C $LORE_ROOT/wiki/<wiki> add .briefing-ledger.json
git -C $LORE_ROOT/wiki/<wiki> commit -m "lore: briefing ledger YYYY-MM-DD"
```

## Important rules

- **One wiki per briefing** — don't mix audiences
- **No duplicates** — the ledger is the source of truth for what's been
  briefed
- **Always show in conversation** — even if the sink fails
- **Keep it short** — under 30 lines
- **Prioritize decisions and open items** — that's what developers need
- **Credentials stay out of the wiki repo** — config references env vars
  or user-local files
