# lore-workflow

Companion plugin to [`lore`](../README.md): deterministic epic/PRD/TDD
workflow skills (orient, grill, to-epic, orchestrate-epic, tdd, debug,
document-epic, implement-issue, seed-epic, domain-modeling).

Empty for now — skills migrate in from `ccatobs/ccat-agent-workflow` in a
later slice (see PRD 0003).

**Dependency direction: `lore-workflow` depends on `lore`; `lore` never
depends on `lore-workflow`.** Workflow skills call `lore`'s CLI/MCP surface
(code map, tier resolver, workflow subcommands); nothing in `lore` core
imports or requires this plugin. This keeps `lore` installable standalone
and lets `lore-workflow` stay opt-in.

Versioned independently from `lore` — see `.claude-plugin/plugin.json`.
