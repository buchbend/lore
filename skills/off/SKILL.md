---
name: lore:off
description: Mute all Lore touchpoints for the current session — hooks
  (SessionStart, PreCompact, Stop, UserPromptSubmit), MCP retrieval,
  and inline "consulted" affordances. Resets at session end. Run with
  "/lore:off" to disable, "/lore:on" to re-enable.
user_invocable: true
---

# Off — per-session mute

Silences Lore for the current session. Useful for demos, screen-shares,
or when you just want a clean context with no auto-injection and no
vault content reaching the model.

## What to do

Run:

```bash
lore off
```

`lore off` writes a per-session sentinel under `$TMPDIR`. Hooks check
for it before any work and exit cleanly; the MCP `_dispatch` returns a
`session_off` refusal envelope without touching the vault.

## What gets muted

- SessionStart, PreCompact, Stop, UserPromptSubmit hooks.
- The capture pipeline (SessionEnd + the SessionStart/PreCompact
  capture invocations) — no transcript ingestion, no curator spawn,
  no ledger writes, no vault output during the muted session.
- Every MCP tool (`lore_search`, `lore_read`, `lore_resume`, …) — they
  return `{"error": {"code": "session_off", …}}` until `/lore:on`.
- Inline `› consulted [[X]]` affordances.

## What still works

- `/lore:on` — clears the sentinel, hooks and MCP resume immediately.
- Explicit local CLI commands you run yourself (`lore search`,
  `lore session`, `lore lint`) — unaffected.

## Related

- `/lore:off citations` — narrower scope: silence only the inline
  affordance; hooks and MCP keep working.
- `/lore:on` — un-mute everything that was muted.
- `/lore:context` — audit what SessionStart had injected before muting.
