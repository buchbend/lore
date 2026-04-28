---
name: lore:quiet
description: Suppress inline "› consulted [[note]]" citation affordances
  for this session while keeping SessionStart auto-injection and MCP
  retrieval. Run with "/lore:quiet" to silence, "/lore:loud" to
  re-enable. (Alias for `/lore:off citations` — kept for backward
  compatibility; will be removed in a future release.)
user_invocable: true
---

# Quiet — silence inline citations

Tells the agent to stop rendering `› consulted [[note-name]]` above
answers that consulted the vault. Useful when citations become noisy
in a long session or when recording terminal output.

## What to do

Run:

```bash
lore off citations
```

The sentinel is checked by SessionStart and by the per-prompt
UserPromptSubmit heartbeat, so the suppression directive lands on the
agent's very next turn — no need to wait for a fresh session.

## What stays the same

- SessionStart one-liner, PreCompact injection, Stop hint — these
  remain unaffected (citations toggle is narrower than `off all`).
- Vault content via `/lore:search`, `/lore:resume`, etc.

## Related

- `/lore:loud` — re-enable inline citations.
- `/lore:off` — mute everything for the session.
- `/lore:context` — see what was loaded at session start.
