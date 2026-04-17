---
name: lore:why
description: Show exactly what Lore auto-injected into this session (the
  output the SessionStart hook emitted). Run with "/lore:why".
user_invocable: true
---

# Why — audit what SessionStart loaded

Shows the full context block Lore injected at session start so you can
verify the repo-scoping, focus note, open items, and staleness flags
that the agent is working from.

## Implementation

Run exactly one command — no catalog inspection, no diagnostic scripts:

```bash
lore hook session-start --cwd "$CLAUDE_PROJECT_DIR" --plain
```

Display the output verbatim. If empty, the hook couldn't resolve a
wiki — tell the user the likely cause (no `LORE_ROOT` set, no matching
wiki in `$LORE_ROOT/wiki/`, or no `.lore-hints.yml` listing the current
repo).

## Do not

- Do **not** run `grep` / `find` / `python3 -c` / catalog introspection.
  One command is enough.
- Do **not** pass flags other than `--cwd` and `--plain`.
- Do **not** read `_catalog.json` / `_index.md` directly — the hook's
  output is the canonical answer.

## Related

- `/lore:off` — mute hooks for the session
- `/lore:quiet` — suppress inline citation affordances
