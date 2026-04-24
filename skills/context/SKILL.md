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

One Bash call, then one text message. Two requirements:

1. Run `lore hook context-log` with `dangerouslyDisableSandbox: true`.
   The command walks `/proc` to find the Claude Code ancestor PID,
   which the default sandbox blocks with a seccomp error on the first
   try. Disabling upfront avoids a wasted retry.
2. After the Bash call returns, output the captured stdout **verbatim
   as your text reply** — inside a fenced code block so whitespace and
   the `──` separators render cleanly. This is essential: Bash tool
   output is collapsed in the UI by default, so echoing it as text is
   what makes the timeline visible inline.

**Do not add a summary, restate the content, or interpret it** — the
user can read it themselves. No preamble, no trailing commentary.
Just the fenced block.

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
