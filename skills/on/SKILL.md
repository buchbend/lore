---
name: lore:on
description: Re-enable Lore hooks for the current session after `/lore:off`
  muted them. Removes the per-session sentinel so SessionStart, PreCompact,
  and inline citation affordances resume immediately. Run with "/lore:on".
user_invocable: true
---

# On — un-mute Lore for this session

Inverse of `/lore:off`. Removes the per-session sentinel that was
silencing Lore hooks and inline affordances, restoring auto-injection
at the next hook firing.

## Behavior

- `/lore:on` — removes `$TMPDIR/lore-off-<session>` sentinel; hooks
  resume on their next invocation
- No-op if no sentinel exists (Lore is already active)

## What returns

- SessionStart auto-injection (one-liner, index, open items)
- PreCompact injection
- Stop prompt
- Inline "consulted [[X]]" affordances

## Related

- `/lore:off` — mute everything for the session
- `/lore:quiet` / `/lore:loud` — toggle only inline citations
- `/lore:context` — see what was last injected
