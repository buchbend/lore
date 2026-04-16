---
name: lore:why
description: Audit what SessionStart auto-injected into this session —
  shows the exact context blob the hook produced, the wiki it resolved
  to, and which repo scoping matched. Run with "/lore:why".
user_invocable: true
---

# Why — audit SessionStart injection

Shows exactly what Lore added to the session at startup so the user can
verify the magic is doing the right thing (and if not, see why).

## Output

```
Scoped repo:  org/name (from `origin` remote at <cwd>)
Wiki:         <name>  (resolved via `.lore-hints.yml` | catalog `repos:` | name substring)
Injected:     ~<N> tokens
Open items:   <count> (from last 7 days)
Stale flags:  <count> (from curator review)

--- injected context ---
<one-liner status>

<full injected block>
---
```

## Implementation

Re-runs the SessionStart hook with verbose provenance:

```bash
lore hook session-start --cwd $CLAUDE_PROJECT_DIR --explain
```

(implementation note: `--explain` flag to be added to `lore_cli.hooks`;
for now the skill falls back to running the hook normally and reading
`_review.md` + the catalog directly.)

## Related

- `/lore:off` — mute hooks for the session
- `/lore:quiet` — suppress inline "consulted [[X]]" affordances but
  keep SessionStart loading
