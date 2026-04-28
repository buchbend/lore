---
name: lore:loud
description: Re-enable inline "› consulted [[note]]" citation affordances
  after `/lore:quiet` silenced them. Does not affect SessionStart
  auto-injection or MCP retrieval — those are always on. Run with
  "/lore:loud". (Alias for `/lore:on citations` — kept for backward
  compatibility; will be removed in a future release.)
user_invocable: true
---

# Loud — re-enable inline citations

Inverse of `/lore:quiet`. Clears the per-session sentinel so the
SessionStart additionalContext stops carrying the suppression
directive.

## What to do

Run:

```bash
lore on citations
```

No-op if quiet was never set.

## What stays the same

- SessionStart one-liner, PreCompact injection, Stop hint — these were
  unaffected by `/lore:quiet` and remain unaffected.
- MCP retrieval (always on regardless of citations toggle).

## Related

- `/lore:quiet` — silence inline citations.
- `/lore:off` / `/lore:on` — mute everything, not just citations.
- `/lore:context` — see what was loaded at session start.
