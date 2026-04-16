---
name: lore:quiet
description: Suppress inline "› consulted [[note]]" citation affordances
  for this session while keeping SessionStart auto-injection and MCP
  retrieval. Run with "/lore:quiet" to silence, "/lore:loud" to
  re-enable.
user_invocable: true
---

# Quiet — silence inline citations

Tells the agent to stop rendering `› consulted [[note-name]]` above
answers that used `lore_search`. Useful when citations become noisy in
a long session or when recording terminal output.

## Behavior

Writes a preference flag for the current session. The agent reads the
flag before emitting citation affordances and skips them when set.

- `/lore:quiet` — silences citations
- `/lore:loud` — re-enables citations
- Resets on session end

## What stays visible

- SessionStart one-liner (`lore: loaded <wiki> …`)
- PreCompact injection
- Stop hint
- Vault content when explicitly asked for

## Related

- `/lore:off` — mute everything, not just citations
- `/lore:why` — see what was loaded at session start
