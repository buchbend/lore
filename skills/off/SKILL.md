---
name: lore:off
description: Mute all Lore hooks for the current session (SessionStart,
  PreCompact, Stop, inline "consulted" affordances). Resets on next
  session. Run with "/lore:off" to disable, "/lore:on" to re-enable.
user_invocable: true
---

# Off — per-session mute

Silences Lore for the current session. Useful for demos, screen-shares,
or when you just want a clean context without auto-injection.

## Behavior

- `/lore:off` — writes `$TMPDIR/lore-off-<session>` sentinel;
  hook commands check for it and exit cleanly
- `/lore:on` — removes the sentinel; hooks resume immediately
- Sentinel is cleared automatically on session end (next SessionStart
  starts fresh)

## What gets muted

- SessionStart auto-injection (no one-liner, no index, no open items)
- PreCompact injection
- Stop prompt
- Inline "consulted [[X]]" affordances

## What still works

- Explicit `/lore:*` commands (`/lore:search`, `/lore:session`,
  `/lore:lint`, etc.) — always active
- MCP tools — always active (the agent can still call them)

## Related

- `/lore:quiet` — silence only inline citations, keep SessionStart
- `/lore:loaded` — audit what SessionStart would have injected
