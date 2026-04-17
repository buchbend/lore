---
name: lore:loaded
description: Show the exact context block Lore injected at session start.
  Resolves the current session's cache via process ancestry so concurrent
  Claude sessions don't cross-talk. Run with "/lore:loaded".
user_invocable: true
---

# Loaded — show what SessionStart injected

Prints the full context block Lore injected at session start so you can
verify the directives, repo-scoping, focus note, open items, and
staleness flags the agent is working from. Read-only — no re-gather, no
LLM, no tool approvals.

## Implementation

SessionStart caches its injected context keyed by the Claude Code
process PID (at `$LORE_CACHE/sessions/<pid>.md`), so two concurrent
Claude sessions never stomp each other. The cache stores the **full
text**, even when the agent-facing inject was truncated to fit the
context budget — so this skill always shows the complete picture.

Run via the Bash tool:

```
lore hook why
```

This walks the process tree up to the Claude Code parent, reads the
per-session cache file, and prints it verbatim. The `Bash(lore *)`
allow-rule already covers it, so no permission prompt.

(The CLI subcommand is still named `lore hook why` for backwards
compatibility with older skill installs; the user-facing skill has
been renamed.)

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

- `/lore:resume` — fresh gather of context (re-runs the work; not the
  same as showing the cache)
- `/lore:off` — mute hooks for the session
- `/lore:quiet` — suppress inline citation affordances
