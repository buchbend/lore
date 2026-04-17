---
name: lore:why
description: Show the exact context block Lore injected at session start.
  Reads from cache — no bash, no permission prompt. Run with "/lore:why".
user_invocable: true
---

# Why — audit what SessionStart loaded

Shows the full context block Lore injected at session start so you can
verify the repo-scoping, focus note, open items, and staleness flags
that the agent is working from.

## Implementation

SessionStart writes its injected context to a cache file. Read it
directly — **do not** run any bash subcommand:

- Primary cache: `$HOME/.cache/lore/last-session-start.md`
- If `LORE_CACHE` is set in the environment, use
  `$LORE_CACHE/last-session-start.md` instead

If the file does not exist, it means SessionStart has not fired yet in
this session (or the hooks are disabled). Tell the user so and suggest:

- Check `~/.claude/settings.json` has the `SessionStart` hook pointing
  at `lore hook session-start`
- Or re-run the installer: `cd <lore-repo> && ./install.sh --with-hooks`

## Do not

- Do **not** invoke `lore hook session-start` via Bash — that re-runs
  the hook and triggers the sandbox path. Read the cached file.
- Do **not** grep the catalog or read wiki notes here. This skill's
  only job is to print the cached injection verbatim.

## Output

Print the file contents exactly as written (it's already markdown),
then add a one-line summary noting the wiki + repo scope for
convenience.

## Related

- `/lore:off` — mute hooks for the session
- `/lore:quiet` — suppress inline citation affordances
