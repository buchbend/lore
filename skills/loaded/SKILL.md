---
name: loaded
description: Show the exact context block Lore injected at session start.
  Resolves the current session's cache via process ancestry so concurrent
  Claude sessions don't cross-talk. Run with "/lore:loaded".
user_invocable: true
---

# Loaded — show what SessionStart injected

Prints the full context block Lore injected at session start. Read-only:
no re-gather, no LLM judgment, no commentary.

## Implementation

```
lore hook why
```

That's it. One Bash call. Print the output verbatim. **Do not add a
summary, restate the content, or interpret it** — the user can read.
The whole point of the skill is "show me the bytes."

The CLI subcommand keeps the legacy `why` name for backwards compat
with older skill installs; the user-facing skill name was renamed
to `loaded` because it matches the SessionStart status line text
(`lore: loaded ...`).

## When the cache is empty

If `lore hook why` reports no cache, SessionStart didn't fire (hooks
disabled, plugin not installed, or running outside a wiki-attached
repo). Print the output and stop — `lore doctor` is the right next
step for diagnosing why, and the user can run it themselves.

## Do not

- Re-narrate the cached output ("the cached SessionStart shows…",
  "Summary:…"). The cache IS the output.
- Invoke `lore hook session-start` to re-generate the cache. Use
  `/lore:resume` for a fresh gather.
- Read wiki notes, grep the catalog, call MCP tools.

## Related

- `/lore:resume` — fresh gather (different from showing the cache)
- `/lore:off` — mute hooks for the session
- `/lore:quiet` — suppress inline citation affordances
