---
name: lore:why
description: Show the exact context block Lore injected at session start.
  Resolves the current session's cache via process ancestry so concurrent
  Claude sessions don't cross-talk. Run with "/lore:why".
user_invocable: true
---

# Why — audit what SessionStart loaded

Shows the full context block Lore injected at session start so you can
verify the repo-scoping, focus note, open items, and staleness flags
that the agent is working from.

## Implementation

SessionStart caches its injected context keyed by the Claude Code
process PID (at `$LORE_CACHE/sessions/<pid>.md`), so two concurrent
Claude sessions never stomp each other. Resolving the right PID
requires walking process ancestry, so this skill uses a small helper.

Run via the Bash tool:

```
lore hook why
```

This walks the process tree up to the Claude Code parent, reads the
per-session cache file, and prints it verbatim. It is **read-only** —
it does not re-run the hook and does not regenerate the context. The
`Bash(lore *)` allow-rule already covers it, so no permission prompt.

## Fallback

If `lore hook why` says no cache was found, SessionStart has not fired
in this session or hooks are disabled. Suggest:

- Check `~/.claude/settings.json` has the `SessionStart` hook pointing
  at `lore hook session-start`
- Or re-run the installer: `cd <lore-repo> && ./install.sh --with-hooks`

If the output is prefixed with a note about "legacy singleton cache",
the current session's per-PID cache is missing and the content may be
from a different concurrent session — flag this to the user.

## Do not

- Do **not** invoke `lore hook session-start` via Bash — that re-runs
  the hook and may surface permission prompts on some setups.
- Do **not** grep the catalog or read wiki notes here. This skill's
  only job is to print the cached injection verbatim.

## Output

Print the command output exactly as returned (it's already markdown),
then add a one-line summary noting the wiki + repo scope for
convenience.

## Related

- `/lore:off` — mute hooks for the session
- `/lore:quiet` — suppress inline citation affordances
