---
name: lore:context
description: Show the full timeline of context Lore injected this session.
  Pure cache read — no live I/O, no re-gather. Run with "/lore:context".
user_invocable: true
---

# Context — show what Lore injected this session

Prints the context log: a timestamped timeline of everything Lore
injected into this session — the initial SessionStart block plus
any mid-session heartbeat updates.

## Implementation

```
lore hook context-log
```

One Bash call. Print the output verbatim. **Do not add a summary,
restate the content, or interpret it** — the user can read.

The output is a timeline:

```
── SessionStart HH:MM ──
lore: active · [[project]] · 2 sessions · 3 issues
[full context body]

── HH:MM ──
new note [[2026-04-23-some-slug]]
  → injected: [[2026-04-23-some-slug]]
```

SessionStart overwrites the log; heartbeat appends. The file is
PID-scoped so concurrent Claude sessions don't cross-talk.

## When the cache is empty

If no context log exists, the command prints a message explaining
that SessionStart hasn't fired. `lore doctor` is the right next
step for diagnosing why — mention it only if the user asks.

## Do not

- Re-narrate the output.
- Invoke `lore hook session-start` to re-generate. Use
  `/lore:resume` for a fresh gather.
- Read wiki notes, grep the catalog, call MCP tools.

## Related

- `/lore:resume` — fresh gather (different from showing the cache)
- `/lore:off` — mute hooks for the session
