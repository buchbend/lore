---
name: lore:loaded
description: Show live capture state AND the exact context block Lore injected at session start.
  Resolves the current session's cache via process ancestry so concurrent
  Claude sessions don't cross-talk. Run with "/lore:loaded".
user_invocable: true
---

# Loaded — show live state + what SessionStart injected

Prints two sections: (1) live state right now (from CaptureState — same
source as `lore status`), (2) the full context block Lore injected at
session start. Read-only: no re-gather, no LLM judgment, no commentary.

## Implementation

```
lore hook why
```

That's it. One Bash call. Print the output verbatim. **Do not add a
summary, restate the content, or interpret it** — the user can read.
The whole point of the skill is "show me the bytes."

The output is structured:

```
── Live state (as of now) ────
<scope, last note, last run, pending, lock — rendered from CaptureState>

── Injected at SessionStart ────
<full cached hook output from SessionStart>
```

Live state comes first because for a Claude session asking "what's the
state?", current matters more than historical. The cache is context.

The CLI subcommand keeps the legacy `why` name for backwards compat
with older skill installs; the user-facing skill name was renamed
to `loaded` because it matches the SessionStart status line text
(`lore: loaded ...`).

## When the cache is empty

If the cache is empty, `lore hook why` still renders the live-state
section and prints an explanatory message in place of the injected
block. SessionStart didn't fire (hooks disabled, plugin not installed,
or running outside a wiki-attached repo). `lore doctor` is the right
next step for diagnosing why — mention it only if the user asks.

For activity questions ("is the curator alive?", "last run results"),
`lore status` is a dedicated CLI view that renders the same live state
without the cached-injection block.

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
