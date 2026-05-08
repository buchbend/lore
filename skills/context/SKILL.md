---
name: lore:context
description: Show the full timeline of context Lore injected this session.
  Pure cache read — no live I/O, no re-gather. Run with "/lore:context".
user_invocable: true
---

# Context — show what Lore injected this session

One Bash call, then one text message:

1. Run `lore hook context-log` with `dangerouslyDisableSandbox: true`
   (it walks `/proc` for the Claude Code ancestor PID; sandboxed
   seccomp blocks that on the first try).
2. Output the captured stdout **verbatim** inside a fenced code block.
   No preamble, no commentary — Bash output is collapsed in the UI by
   default, so echoing it as text is what makes the timeline visible.

If the log is empty, the command prints a message; relay it as-is.
Mention `lore doctor` only if the user asks why.

## Do not

- Re-narrate the output.
- Invoke `lore hook session-start` to re-generate — use `/lore:resume`
  for a fresh gather.
- Read wiki notes, grep the catalog, or call MCP tools.

## Related

- `/lore:resume` — fresh gather (different from showing the cache)
- `lore off` — mute hooks for the session
