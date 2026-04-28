---
name: lore:on
description: Re-enable Lore for the current session after `/lore:off`
  muted it. Removes the per-session sentinel so hooks and MCP resume
  immediately at the next firing. Run with "/lore:on".
user_invocable: true
---

# On — un-mute Lore for this session

Inverse of `/lore:off`. Removes the per-session sentinel that was
silencing hooks and MCP retrieval, restoring normal behavior.

## What to do

Run:

```bash
lore on
```

No-op if the sentinel was never set (Lore is already active).

## What returns

- SessionStart auto-injection (one-liner, index, open items).
- PreCompact / Stop / UserPromptSubmit hooks.
- MCP tools (`lore_search`, `lore_read`, …).
- Inline `› consulted [[X]]` affordances (unless `/lore:off citations`
  is also active — see `/lore:on citations`).

## Related

- `/lore:off` — mute everything for the session.
- `/lore:on citations` — un-mute only inline citations.
- `/lore:context` — see what was last injected.
