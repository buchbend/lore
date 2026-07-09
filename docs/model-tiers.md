# Model tiers

Skills and subagent spawns delegate work by **semantic tier**, never by
concrete model name: a tier says how much capability a step needs, and
`lore tier resolve <tier>` maps that to a concrete model for the host
currently running. That indirection is what lets a plugin move from
Claude Code to another host without editing every prompt — only the
table in `lib/lore_core/tiers/table.py` changes.

The no-hardcoded-model-name contract is enforced in this repo by
`tests/test_workflow_plugin_structural.py`. For which workflow *stage*
runs at which tier, and how strictly that mapping is enforced, see
["Model tiers"](conventions.md#model-tiers) in `docs/conventions.md`.

## Tiers

Four tiers, ordered strongest -> cheapest:

| Tier | Semantic role | Claude Code model |
|------|----------------|--------------------|
| `frontier` | Strongest reasoning: orchestration, grilling / synthesis. | `claude-opus-4-8` (the session's driving model) |
| `strong` | Crosscheck / review; architectural or cross-cutting implementation. | `claude-opus-4-8` |
| `mid` | Exploration / gathering; mechanical implementation. | `claude-sonnet-5` |
| `cheap` | Bulk-mechanical sub-tasks only — never a stage's default. | `claude-haiku-4-5` |

**Cursor's column is PROVISIONAL** — a best guess seeded ahead of any
real mileage on that host. Treat it as a starting point, not ground
truth; update `lib/lore_core/tiers/table.py` once the plugin actually
runs there.

## Rules (carried over from the source table)

- **Ordinal / collapse.** `frontier` > `strong` > `mid` > `cheap`. A
  host with no distinct model for two adjacent tiers collapses them
  onto the same model (Claude Code: `frontier` and `strong` both
  resolve to Opus).
- **Fallback.** An unregistered host or tier is never silently
  guessed — `resolve_tier` / `lore tier resolve` raise
  `TierResolutionError` loudly instead.
- **Cheap reservation.** `cheap` is reserved for explicitly
  bulk-mechanical work (large volumes of rote, low-judgement edits).
  It is no stage's default.

## Resolving a tier

```
lore tier resolve mid
lore tier resolve frontier --host cursor
```

Also exposed as the `lore_tier_resolve` MCP tool (`tier`, optional
`host`) — same resolution, for callers that can't shell out.

## User overrides

Any table cell can be overridden per vault, without touching the
shipped table, via `$LORE_ROOT/.lore/config.yml`:

```yaml
tiers:
  overrides:
    claude:
      frontier: claude-opus-4-9
```

Only the tiers you list are overridden; everything else falls through
to `lib/lore_core/tiers/table.py`. See `lib/lore_core/root_config.py:TierConfig`
and `tests/test_tiers.py::test_config_override_wins_over_table_default`.
