---
name: lore:loud
description: Re-enable inline "› consulted [[note]]" citation affordances
  after `/lore:quiet` silenced them. Does not affect SessionStart
  auto-injection or MCP retrieval — those are always on. Run with
  "/lore:loud".
user_invocable: true
---

# Loud — re-enable inline citations

Inverse of `/lore:quiet`. Clears the per-session preference flag so the
agent resumes rendering `› consulted [[note-name]]` above answers that
used `lore_search`.

## Behavior

- `/lore:loud` — clears the quiet flag for the current session;
  citations appear on the next answer that consults the vault
- No-op if quiet was never set

## What stays the same

- SessionStart one-liner, PreCompact injection, Stop hint — these were
  unaffected by `/lore:quiet` and remain unaffected.
- Vault content when explicitly asked for.

## Related

- `/lore:quiet` — silence inline citations
- `/lore:off` / `/lore:on` — mute everything, not just citations
- `/lore:context` — see what was loaded at session start
